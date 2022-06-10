# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest.mock as mock
from pathlib import Path

import pytest
import yaml
from ops.model import BlockedStatus, WaitingStatus
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
def mock_ca_cert(tmpdir):
    ca_cert = Path(tmpdir) / "ca.crt"
    with mock.patch.object(VsphereCloudProviderCharm, "CA_CERT_PATH", ca_cert):
        yield ca_cert


@pytest.fixture()
def control_plane(harness: Harness):
    rel_id = harness.add_relation("external-cloud-provider", "kubernetes-control-plane")
    harness.add_relation_unit(rel_id, "kubernetes-control-plane/0")
    harness.add_relation_unit(rel_id, "kubernetes-control-plane/1")


@pytest.fixture()
def integrator():
    with mock.patch("charm.VsphereIntegrationRequires") as mocked:
        vsphereintegrator = mocked.return_value
        vsphereintegrator.vsphere_ip = "1.2.3.4"
        vsphereintegrator.user = "alice"
        vsphereintegrator.password = "bob"
        vsphereintegrator.datacenter = "Elbonia"
        vsphereintegrator.evaluate_relation.return_value = None
        yield vsphereintegrator


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
        kube_control.registry_location = "rocks.canonical.com/cdk"
        yield kube_control


def test_waits_for_integrator(harness):
    harness.begin_with_initial_hooks()
    charm = harness.charm
    assert isinstance(charm.unit.status, BlockedStatus)
    assert charm.unit.status.message == "Missing required vsphere-integration relation"


@pytest.mark.usefixtures("integrator")
def test_waits_for_certificates(harness):
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


@mock.patch("requires_kube_control.KubeControlRequires.create_kubeconfig")
@pytest.mark.usefixtures("integrator", "certificates")
def test_waits_for_kube_control(mock_create_kubeconfig, harness):
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
    assert (
        charm.unit.status.message
        == "Provider manifests waiting for definition of control-node-selector"
    )


@pytest.mark.usefixtures("integrator", "certificates", "kube_control", "control_plane")
def test_waits_for_config(harness: Harness, lk_client, caplog):
    harness.begin_with_initial_hooks()

    lk_client().list.return_value = [mock.Mock(**{"metadata.annotations": {}})]
    caplog.clear()
    harness.update_config(
        {
            "server": "vsphere.local",
            "username": "alice",
            "password": "s3cr3t",
            "datacenter": "dc1",
            "control-node-selector": 'gcp.io/my-control-node=""',
        }
    )
    provider_messages = [r.message for r in caplog.records if "provider" in r.filename]
    storage_messages = [r.message for r in caplog.records if "storage" in r.filename]

    assert provider_messages == [
        "Applying provider secret data for server vsphere.local",
        "Applying provider ConfigMap Data for vcenter dc1",
        'Applying provider Control Node Selector as gcp.io/my-control-node: ""',
    ]
    assert storage_messages == [
        "Creating storage secret data for server vsphere.local",
        "Creating storage class csi-vsphere-default",
        'Applying storage Control Node Selector as gcp.io/my-control-node: ""',
        "Setting storage deployment replicas to 2",
    ]

    caplog.clear()
    harness.update_config(
        {
            "server": "",
            "username": "",
            "password": "",
            "datacenter": "",
            "control-node-selector": "",
            "image-registry": "dockerhub.io",
        }
    )
    provider_messages = [r.message for r in caplog.records if "provider" in r.filename]
    storage_messages = [r.message for r in caplog.records if "storage" in r.filename]

    assert provider_messages == [
        "Applying provider secret data for server 1.2.3.4",
        "Applying provider ConfigMap Data for vcenter Elbonia",
        'Applying provider Control Node Selector as juju-application: "kubernetes-control-plane"',
    ]
    assert storage_messages == [
        "Creating storage secret data for server 1.2.3.4",
        "Creating storage class csi-vsphere-default",
        'Applying storage Control Node Selector as juju-application: "kubernetes-control-plane"',
        "Setting storage deployment replicas to 2",
    ]
