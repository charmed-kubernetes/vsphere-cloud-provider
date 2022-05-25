# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import json
import unittest.mock as mock
from pathlib import Path

import pytest
import yaml
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus
from ops.testing import Harness

from charm import VsphereCloudProviderCharm


@pytest.fixture
def harness():
    harness = Harness(VsphereCloudProviderCharm)
    try:
        yield harness
    finally:
        harness.cleanup()


@pytest.fixture(autouse=True)
def lk_client():
    with mock.patch("lightkube.Client") as mock_lightkube:
        yield mock_lightkube


def test_ccm(harness, lk_client):
    harness.set_leader(True)
    harness.begin_with_initial_hooks()
    assert isinstance(harness.charm.unit.status, BlockedStatus)

    # Add the kube-control relation
    rel_cls = type(harness.charm.kube_control)
    rel_cls.relation = property(rel_cls.relation.func)
    rel_cls._data = property(rel_cls._data.func)
    rel_id = harness.add_relation("kube-control", "kubernetes-control-plane")
    assert isinstance(harness.charm.unit.status, WaitingStatus)
    harness.add_relation_unit(rel_id, "kubernetes-control-plane/0")
    assert isinstance(harness.charm.unit.status, WaitingStatus)
    harness.update_relation_data(
        rel_id,
        "kubernetes-control-plane/0",
        yaml.safe_load(Path("tests/data/kube_control_data.yaml").read_text()),
    )
    assert isinstance(harness.charm.unit.status, BlockedStatus)

    # Add the external-cloud-provider relation
    harness.add_relation("external-cloud-provider", "kubernetes-control-plane")
    assert isinstance(harness.charm.unit.status, BlockedStatus)

    # Add the vsphere-integration relation
    rel_cls = type(harness.charm.integrator)
    rel_cls.relation = property(rel_cls.relation.func)
    rel_cls._data = property(rel_cls._data.func)
    rel_id = harness.add_relation("vsphere-integration", "integrator")
    assert isinstance(harness.charm.unit.status, WaitingStatus)
    harness.add_relation_unit(rel_id, "integrator/0")
    assert isinstance(harness.charm.unit.status, WaitingStatus)
    harness.update_relation_data(
        rel_id,
        "integrator/0",
        {
            "vsphere_ip": json.dumps("vsphere.local"),
            "datacenter": json.dumps("datacenter"),
            "user": json.dumps("username"),
            "password": json.dumps("password"),
            "datastore": json.dumps("datastore"),
            "repool_path": json.dumps("repool_path"),
        },
    )
    assert isinstance(harness.charm.unit.status, ActiveStatus)
    harness.remove_relation(rel_id)
    assert isinstance(harness.charm.unit.status, BlockedStatus)

    lk_client().list.return_value = [mock.Mock(**{"metadata.annotations": {}})]
    harness.update_config(
        {
            "server": "vsphere.local",
            "username": "alice",
            "password": "s3cr3t",
            "datacenter": "dc1",
            "control-node-selector": 'gcp.io/my-control-node=""',
        }
    )
    assert isinstance(harness.charm.unit.status, ActiveStatus)
