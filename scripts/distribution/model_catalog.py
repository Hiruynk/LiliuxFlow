# SPDX-License-Identifier: Apache-2.0
"""Offline optional checkpoint catalog. Registration never downloads or loads weights."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re

from common import DistributionError, no_symlinks, read_object, relative_path
from profile_registry import SHARED, load_registry

MODEL_ID = 'qwen3.8-flash-next-lily-q4'
INVENTORY_PATH = 'manifests/distribution/checkpoint-files.json'
MANIFEST_PATH = 'manifests/distribution/native-sources.json'
LICENSE_PATH = 'docs/productization/MODEL_LICENSE.txt'
HELPER_PROJECT = 'services/model-installer'
HUB_VERSION = '1.1.4'


@dataclass(frozen=True)
class OptionalModel:
    model_id: str
    entry: dict
    inventory: dict
    inventory_path: Path
    profiles: tuple

    @property
    def total_bytes(self):
        return sum(row['size_bytes'] for row in self.inventory['files'])

    def public_info(self):
        return {'model_id': self.model_id, **self.entry, 'file_count': len(self.inventory['files']),
                'size_bytes': self.total_bytes, 'size_gib': round(self.total_bytes / 1024**3, 2),
                'profiles': [{'model': p.public_alias, 'context_tokens': p.context_tokens,
                              'default': p.profile_id == 'ctx64k'} for p in self.profiles],
                'network_operation': False, 'downloaded_bytes': 0}


def load_catalog(source_root):
    source = no_symlinks(source_root)
    manifest = read_object(source / MANIFEST_PATH)
    entries = manifest.get('optional_models')
    if manifest.get('schema_version') != 1 or not isinstance(entries, dict) or set(entries) != {MODEL_ID}:
        raise DistributionError('optional model catalog differs from the finite supported set')
    entry = entries[MODEL_ID]
    expected = {'kind': 'huggingface-model', 'repository': SHARED['model_repository'],
                'revision': SHARED['model_revision'], 'inventory': INVENTORY_PATH,
                'format': SHARED['model_format'], 'engine': 'lily', 'install_by_default': False,
                'homepage': 'https://huggingface.co/' + SHARED['model_repository'],
                'license_name': 'Qwen Community License 1.0', 'license_file': LICENSE_PATH,
                'redistribute_payload': False}
    if not isinstance(entry, dict) or set(entry) != set(expected) or any(type(entry[k]) is not type(v) or entry[k] != v for k, v in expected.items()):
        raise DistributionError('optional model repository, revision, format or license policy differs')
    inventory_path = no_symlinks(source / entry['inventory'])
    inventory = read_object(inventory_path)
    if (inventory.get('schema_version') != 1
        or any(inventory.get(k) != entry[k] for k in ('repository', 'revision', 'format'))
        or not re.fullmatch('[a-f0-9]{64}', str(inventory.get('checkpoint_manifest_sha256', '')))):
        raise DistributionError('checkpoint inventory identity differs from the optional model catalog')
    files = inventory.get('files')
    if not isinstance(files, list) or len(files) != 67:
        raise DistributionError('checkpoint inventory must contain the canonical 67 files')
    paths = set()
    for row in files:
        if (not isinstance(row, dict) or set(row) != {'path', 'size_bytes', 'sha256'}
            or type(row.get('size_bytes')) is not int or row['size_bytes'] < 0
            or not re.fullmatch('[a-f0-9]{64}', str(row.get('sha256', '')))):
            raise DistributionError('checkpoint inventory file record is invalid')
        relative_path(row['path'])
        if row['path'] in paths or any(c in row['path'] for c in '*?[]'):
            raise DistributionError('checkpoint inventory contains duplicate or wildcard paths')
        paths.add(row['path'])
    if not {'config.json', 'model.safetensors.index.json', 'LICENSE'}.issubset(paths):
        raise DistributionError('checkpoint inventory omits runtime metadata or its license')
    registry = load_registry(source)
    return {MODEL_ID: OptionalModel(MODEL_ID, dict(entry), inventory, inventory_path, registry.profiles)}


def select_model(source_root, model_id):
    catalog = load_catalog(source_root)
    if model_id not in catalog:
        raise DistributionError('model is outside the finite optional catalog; use models list')
    return catalog[model_id]
