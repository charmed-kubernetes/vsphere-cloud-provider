# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import json
from unittest.mock import Mock

import pytest
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


@pytest.fixture
def lk_client(monkeypatch):
    monkeypatch.setattr(
        "charms.vsphere_cloud_provider_operator.v0.lightkube_helpers.Client",
        client := Mock(name="lightkube.Client"),
    )
    return client


def test_ccm(harness, lk_client):
    harness.set_leader(True)
    harness.begin_with_initial_hooks()
    assert isinstance(harness.charm.unit.status, BlockedStatus)

    # Remove caching from properties (happens automatically for the
    # cloud-config relation provider).
    rel_cls = type(harness.charm.integrator)
    del harness.charm.integrator.relation
    rel_cls.relation = property(rel_cls.relation.func)
    del harness.charm.integrator._data
    rel_cls._data = property(rel_cls._data.func)

    harness.add_relation("external-cloud-provider", "kubernetes-control-plane")
    assert isinstance(harness.charm.unit.status, BlockedStatus)

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

    lk_client().list.return_value = [Mock(**{"metadata.annotations": {}})]
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
