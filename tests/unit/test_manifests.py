# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.

import unittest.mock as mock
from collections import namedtuple

import pytest
from lightkube import ApiError

from manifests import Manifests, _HashableResource, _NamespaceKind


def test_namespace_kind():
    assert str(_NamespaceKind("object", "default")) == "object/default"
    assert str(_NamespaceKind("object", None)) == "object"


@pytest.mark.parametrize("namespace", [None, "default"])
def test_hashable_resource(namespace):
    rsc_obj = mock.MagicMock()
    rsc_obj.metadata.name = "test-resource"
    rsc_obj.metadata.namespace = namespace
    hr = _HashableResource(rsc_obj)
    assert str(hr) == f"MagicMock/{namespace+'/' if namespace else ''}test-resource"

    hr2 = _HashableResource(rsc_obj)
    assert hr == hr2
    assert len({hr, hr2}) == 1


@pytest.fixture
def test_manifest():
    class TestManifests(Manifests):
        def __init__(self):
            self.data = {}
            super().__init__("test-manifest")

        @property
        def config(self):
            return self.data

    yield TestManifests()


def test_releases_list(test_manifest):
    assert len(test_manifest.releases), "more than 1 release should exist"
    assert test_manifest.releases[0] == "v1.2"


@pytest.mark.parametrize("release, uniqs", [("v1.2", 6), (None, 7)])
def test_resources_version(test_manifest, release, uniqs):
    test_manifest.data["release"] = release
    rscs = test_manifest.resources
    assert (
        len(rscs) == uniqs
    ), f"{uniqs} unique namespace kind resources in {test_manifest.current_release}"

    key = _NamespaceKind("ServiceAccount", "kube-system")
    assert len(rscs[key]) == 1, "1 service account in kube-system namespace"
    element = next(iter(rscs[key]))
    assert element.metadata.namespace == "kube-system"
    assert element.metadata.name == "cloud-controller-manager"


def mock_get_responder(klass, name, namespace=None, labels=None):
    response = mock.MagicMock(spec=klass)
    response.metadata.name = name
    response.metadata.namespace = namespace
    return response


def test_status(test_manifest):
    Condition = namedtuple("Condition", "status,type")
    with mock.patch.object(test_manifest, "client", new_callable=mock.PropertyMock) as mock_client:
        mock_client.get.return_value.status.conditions = [Condition("False", "Ready")]
        resource_status = test_manifest.status()
    assert mock_client.get.call_count == 7
    # Because mock_client.get.return_value returns the same for all 7 resources
    # The _HashableResource is the same for each.
    assert len(resource_status) == 1


def test_expected_resources(test_manifest):
    with mock.patch.object(test_manifest, "client", new_callable=mock.PropertyMock) as mock_client:
        mock_client.get.side_effect = mock_get_responder
        rscs = test_manifest.expected_resources()
    assert mock_client.get.call_count == 7

    key = _NamespaceKind("ServiceAccount", "kube-system")
    assert len(rscs[key]) == 1, "1 service account in kube-system namespace"
    element = next(iter(rscs[key]))
    assert element.metadata.namespace == "kube-system"
    assert element.metadata.name == "cloud-controller-manager"


def mock_list_responder(klass, namespace=None, labels=None):
    response = mock.MagicMock(spec=klass)
    response.metadata.name = "mock-item"
    response.metadata.namespace = namespace
    response.metadata.labels = labels
    return [response]


def test_active_resources(test_manifest):
    with mock.patch.object(test_manifest, "client", new_callable=mock.PropertyMock) as mock_client:
        mock_client.list.side_effect = mock_list_responder
        rscs = test_manifest.active_resources()
    assert mock_client.list.call_count == 7

    key = _NamespaceKind("ServiceAccount", "kube-system")
    assert len(rscs[key]) == 1, "1 service account in kube-system namespace"
    element = next(iter(rscs[key]))
    assert element.metadata.namespace == "kube-system"
    assert element.metadata.name == "mock-item"


def test_delete_no_resources(test_manifest):
    with mock.patch.object(test_manifest, "client", new_callable=mock.PropertyMock) as mock_client:
        test_manifest.delete_resource()
    mock_client.delete.assert_not_called()


def test_delete_one_resource(test_manifest, caplog):
    rscs = test_manifest.resources
    key = _NamespaceKind("Secret", "kube-system")
    element = next(iter(rscs[key]))
    with mock.patch.object(test_manifest, "client", new_callable=mock.PropertyMock) as mock_client:
        test_manifest.delete_resource(element)
    mock_client.delete.assert_called_once_with(
        type(element.rsc), "vsphere-cloud-secret", namespace="kube-system"
    )
    assert caplog.messages[0] == "Deleting Secret/kube-system/vsphere-cloud-secret"


def test_delete_current_resources(test_manifest, caplog):
    with mock.patch.object(test_manifest, "client", new_callable=mock.PropertyMock) as mock_client:
        test_manifest.delete_manifests()
    assert len(caplog.messages) == 7, "Should delete the 7 resources in this release"
    assert all(msg.startswith("Deleting") for msg in caplog.messages)

    rscs = test_manifest.resources
    key = _NamespaceKind("Secret", "kube-system")
    element = next(iter(rscs[key]))
    mock_client.delete.assert_any_call(
        type(element.rsc), "vsphere-cloud-secret", namespace="kube-system"
    )


@pytest.fixture()
def api_error_klass():
    class TestApiError(ApiError):
        status = mock.MagicMock()

        def __init__(self):
            pass

    yield TestApiError


@pytest.mark.parametrize(
    "status, log_format",
    [
        ("deleting an item that is not found", "Ignoring not found error: {0}"),
        ("(unauthorized) Sorry Dave, I cannot do that", "Unauthorized error ignored: {0}"),
    ],
    ids=["Not found ignored", "Unauthorized ignored"],
)
def test_delete_resource_errors(test_manifest, api_error_klass, caplog, status, log_format):
    rscs = test_manifest.resources
    key = _NamespaceKind("Secret", "kube-system")
    element = next(iter(rscs[key]))
    api_error_klass.status.message = status

    with mock.patch.object(test_manifest, "client", new_callable=mock.PropertyMock) as mock_client:
        mock_client.delete.side_effect = api_error_klass
        test_manifest.delete_resource(element, ignore_unauthorized=True, ignore_not_found=True)
    mock_client.delete.assert_called_once_with(
        type(element.rsc), "vsphere-cloud-secret", namespace="kube-system"
    )
    assert caplog.messages[0] == "Deleting Secret/kube-system/vsphere-cloud-secret"
    assert caplog.messages[1] == log_format.format(status)


@pytest.mark.parametrize(
    "status, log_format",
    [
        (
            "maybe the dingo ate your cloud-secret",
            "ApiError encountered while attempting to delete resource: {0}",
        ),
        (None, "ApiError encountered while attempting to delete resource."),
    ],
    ids=["Unignorable status", "No status message"],
)
def test_delete_resource_raised(test_manifest, api_error_klass, caplog, status, log_format):
    rscs = test_manifest.resources
    key = _NamespaceKind("Secret", "kube-system")
    element = next(iter(rscs[key]))
    api_error_klass.status.message = status

    with mock.patch.object(test_manifest, "client", new_callable=mock.PropertyMock) as mock_client:
        mock_client.delete.side_effect = api_error_klass
        with pytest.raises(api_error_klass):
            test_manifest.delete_resource(element, ignore_unauthorized=True, ignore_not_found=True)
    mock_client.delete.assert_called_once_with(
        type(element.rsc), "vsphere-cloud-secret", namespace="kube-system"
    )
    assert caplog.messages[0] == "Deleting Secret/kube-system/vsphere-cloud-secret"
    assert caplog.messages[1] == log_format.format(status)
