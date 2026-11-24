#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""KernelPrism vendor adapter registry (implements the 04-vendor-spi §2 manifest contract).

- Adapter directory: <anvil_root>/apps/kernel-prism/adapters/<vendor_id>/manifest.json
- A minimal usable adapter is a single manifest.json (declaration only), backed by the
  platform's generic 8-stage capability.
- License isolation: the platform runs zero vendor code; vendor code lives only in its
  adapter directory.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

MANIFEST_SCHEMA = {
    'vendor_id', 'display_name', 'version', 'source',
    'devices', 'dsls', 'stages', 'license',
}
STAGE_IDS = ('v0_understand', 'v1_analyze', 'v2_generate', 'v3_compile',
             'v4_verify', 'v5_profile', 'v6_optimize', 'v7_register')
LICENSE_RESTRICT = {
    'cann-osl-v2.0': {'id': 'CANN OSL v2.0',
                      'restrictions': ['huawei-processor-only', 'no-sublicense', 'revocable'],
                      'install_consent_required': True},
    'none': {'id': 'none', 'restrictions': [], 'install_consent_required': False},
}


def _require(cond: bool, msg: str):
    if not cond:
        raise ValueError(f"manifest validation failed: {msg}")


class VendorRegistry:
    """Adapter discovery + schema validation + lifecycle registration."""

    def __init__(self, anvil_root: str = ''):
        """When anvil_root is empty, auto-detect the repo (<repo>/adapters), so the
        registry works in either a standalone repo or an assembled anvil app."""
        if not anvil_root:
            anvil_root = os.environ.get('ANVIL_ROOT', '')
        if anvil_root:
            self.adapters_dir = os.path.join(anvil_root, 'apps', 'kernel-prism', 'adapters')
        else:
            self.adapters_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'adapters')
        self.anvil_root = anvil_root
        self.registry_path = os.path.join(self.adapters_dir, '.registry.json')

    # ---------- discovery ----------

    def _adapter_dir(self, vendor_id: str) -> str:
        return os.path.join(self.adapters_dir, vendor_id)

    def load(self, vendor_id: str) -> Optional[Dict[str, Any]]:
        """Load and validate a manifest (does not touch the registry)."""
        mpath = os.path.join(self._adapter_dir(vendor_id), 'manifest.json')
        if not os.path.exists(mpath):
            return None
        with open(mpath, 'r', encoding='utf-8') as f:
            m = json.load(f)
        self.validate_manifest(m)
        m['_adapter_dir'] = self._adapter_dir(vendor_id)
        m['stages_list'] = [sid for sid in STAGE_IDS
                            if m.get('stages', {}).get(sid, {}).get('enabled', False)]
        m['health'] = self._health(m)
        return m

    def _health(self, m: Dict[str, Any]) -> str:
        probe_path = m.get('entrypoints', {}).get('probe')
        if probe_path and os.path.exists(os.path.join(m['_adapter_dir'], probe_path)):
            return 'probe-ok'
        return 'declared-only' if m.get('stages_list') else 'bare-minimal'

    def list_all(self) -> List[Dict[str, Any]]:
        rows = []
        if not os.path.isdir(self.adapters_dir):
            return rows
        for name in sorted(os.listdir(self.adapters_dir)):
            if name.startswith('.') or not os.path.isdir(self._adapter_dir(name)):
                continue
            m = self.load(name)
            if m:
                rows.append(m)
        return rows

    # ---------- lifecycle ----------

    def add(self, vendor_id: str) -> Dict[str, Any]:
        m = self.load(vendor_id)
        if m is None:
            raise FileNotFoundError(
                f"adapter directory not found: {self._adapter_dir(vendor_id)} (expected manifest.json)")
        lic = m.get('license', {})
        if lic.get('install_consent_required'):
            # consent is a deployment-time action (invoked only after explicit human
            # confirmation per user policy); record it here
            self._record_registry(vendor_id, m)
        return m

    def _record_registry(self, vendor_id: str, m: Dict[str, Any]) -> None:
        os.makedirs(self.adapters_dir, exist_ok=True)
        reg = {}
        if os.path.exists(self.registry_path):
            with open(self.registry_path, 'r', encoding='utf-8') as f:
                reg = json.load(f)
        reg[vendor_id] = {
            'vendor_id': vendor_id,
            'version': m['version'],
            'license_consented': True,
            'devices': m.get('devices', []),
            'stages': m.get('stages_list', []),
        }
        tmp = self.registry_path + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(reg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.registry_path)

    # ---------- validation ----------

    def validate_manifest(self, m: Dict[str, Any]) -> None:
        _require(isinstance(m, dict), 'manifest must be a JSON object')
        missing = MANIFEST_SCHEMA - set(m)
        _require(not missing, f'missing fields: {sorted(missing)}')
        _require(m['vendor_id'].isalnum() and m['vendor_id'].islower(),
                 f"vendor_id must be lowercase alphanumeric: {m['vendor_id']}")
        _require(m['vendor_id'] == os.path.basename(m.get('_adapter_dir', m['vendor_id'])) or
                 '_adapter_dir' not in m,
                 'vendor_id must match its directory name')
        _require(isinstance(m.get('devices'), list) and m['devices'], 'devices must be a non-empty list')
        _require(isinstance(m.get('dsls'), list) and m['dsls'], 'dsls must be a non-empty list')
        st = m.get('stages', {})
        _require(isinstance(st, dict), 'stages must be a dict')
        for sid in st:
            _require(sid in STAGE_IDS, f'unknown stage id: {sid}')
            _require('enabled' in st[sid], f'stages.{sid}.enabled is missing')
        lic = m.get('license', {})
        _require(isinstance(lic, dict) and 'id' in lic, 'license.id is missing')
        ep = m.get('entrypoints', {})
        _require(isinstance(ep, dict), 'entrypoints must be a dict')
        for slot in ('probe', 'status'):
            p = ep.get(slot)
            if p:
                _require(isinstance(p, str), f'entrypoints.{slot} must be a relative path string')
