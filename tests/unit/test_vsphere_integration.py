# Copyright 2023 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

from pathlib import Path

import yaml

from requires_vsphere_integration import VsphereIntegrationData


def test_parse_relation_data():
    d = (yaml.safe_load(Path("tests/data/vsphere_integration_data.yaml").read_text()),)
    loaded = VsphereIntegrationData(**d[0])
    assert loaded.password == "<super-secret>"
