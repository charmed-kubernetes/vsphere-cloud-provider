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
    with mock.patch("backend.LightKubeHelpers") as mock_lightkube:
        yield mock_lightkube


@pytest.fixture(autouse=True)
def mock_ca_cert(tmpdir):
    ca_cert = Path(tmpdir) / "ca.crt"
    with mock.patch.object(VsphereCloudProviderCharm, "CA_CERT_PATH", ca_cert):
        yield ca_cert


@pytest.fixture()
def certificates():
    with mock.patch("charm.CertificatesRequires") as mocked:
        certificates = mocked.return_value
        certificates.ca = "abcd"
        certificates.evaluate_relation.return_value = None
        yield certificates


@pytest.fixture()
def kube_control():
    with mock.patch("charm.KubeControlRequires") as mocked:
        kube_control = mocked.return_value
        kube_control.evaluate_relation.return_value = None
        yield kube_control


def test_waits_for_certificates(harness):
    harness.set_leader(True)
    harness.begin_with_initial_hooks()
    charm = harness.charm
    assert isinstance(charm.unit.status, BlockedStatus)
    assert charm.unit.status.message == "Missing required certificates"

    # Test adding the certificates relation
    rel_cls = type(charm.certificates)
    rel_cls.relation = property(rel_cls.relation.func)
    rel_cls._data = property(rel_cls._data.func)
    rel_id = harness.add_relation("certificates", "easyrsa")
    assert isinstance(charm.unit.status, WaitingStatus)
    assert charm.unit.status.message == "Waiting for certificates"
    harness.add_relation_unit(rel_id, "easyrsa/0")
    assert isinstance(charm.unit.status, WaitingStatus)
    assert charm.unit.status.message == "Waiting for certificates"
    harness.update_relation_data(
        rel_id,
        "easyrsa/0",
        yaml.safe_load(Path("tests/data/certificates_data.yaml").read_text()),
    )
    assert isinstance(charm.unit.status, BlockedStatus)
    assert charm.unit.status.message == "Missing required kube-control relation"


@mock.patch("kube_control_requires.KubeControlRequires.create_kubeconfig")
@pytest.mark.usefixtures("certificates")
def test_waits_for_kube_control(mock_create_kubeconfig, harness):
    harness.set_leader(True)
    harness.begin_with_initial_hooks()
    charm = harness.charm
    assert isinstance(charm.unit.status, BlockedStatus)
    assert charm.unit.status.message == "Missing required kube-control relation"

    # Add the kube-control relation
    rel_cls = type(charm.kube_control)
    rel_cls.relation = property(rel_cls.relation.func)
    rel_cls._data = property(rel_cls._data.func)
    rel_id = harness.add_relation("kube-control", "kubernetes-control-plane")
    assert isinstance(charm.unit.status, WaitingStatus)
    assert charm.unit.status.message == "Waiting for kube-control relation"

    harness.add_relation_unit(rel_id, "kubernetes-control-plane/0")
    assert isinstance(charm.unit.status, WaitingStatus)
    assert charm.unit.status.message == "Waiting for kube-control relation"
    mock_create_kubeconfig.assert_not_called()

    harness.update_relation_data(
        rel_id,
        "kubernetes-control-plane/0",
        yaml.safe_load(Path("tests/data/kube_control_data.yaml").read_text()),
    )
    mock_create_kubeconfig.assert_has_calls(
        [
            mock.call(charm.CA_CERT_PATH, "/root/.kube/config", "root", charm.unit.name),
            mock.call(charm.CA_CERT_PATH, "/home/ubuntu/.kube/config", "ubuntu", charm.unit.name),
        ]
    )
    assert isinstance(charm.unit.status, BlockedStatus)
    assert charm.unit.status.message == "Missing required config or integrator"


@pytest.mark.usefixtures("certificates", "kube_control")
def test_waits_for_config(harness, lk_client):
    # Add the external-cloud-provider relation
    harness.set_leader(True)
    harness.begin_with_initial_hooks()
    charm = harness.charm
    harness.add_relation("external-cloud-provider", "kubernetes-control-plane")
    assert isinstance(charm.unit.status, BlockedStatus)
    assert charm.unit.status.message == "Missing required config or integrator"

    # Add the vsphere-integration relation
    rel_cls = type(charm.integrator)
    rel_cls.relation = property(rel_cls.relation.func)
    rel_cls._data = property(rel_cls._data.func)
    rel_id = harness.add_relation("vsphere-integration", "integrator")
    assert isinstance(charm.unit.status, WaitingStatus)
    assert charm.unit.status.message == "Waiting for integrator"

    harness.add_relation_unit(rel_id, "integrator/0")
    assert isinstance(charm.unit.status, WaitingStatus)
    assert charm.unit.status.message == "Waiting for integrator"

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
    assert isinstance(charm.unit.status, ActiveStatus)

    harness.remove_relation(rel_id)
    assert isinstance(charm.unit.status, BlockedStatus)
    assert charm.unit.status.message == "Missing required config or integrator"

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
    assert isinstance(charm.unit.status, ActiveStatus)
