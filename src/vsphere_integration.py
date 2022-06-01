# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Implementation of vsphere-integration interface.

This only implements the requires side, currently, since the integrator
is still using the Reactive Charm framework self.
"""
import json
import logging
from backports.cached_property import cached_property

from ops.framework import Object


log = logging.getLogger(__name__)


class VsphereIntegrationRequires(Object):
    """Requires side of vsphere-integration relation."""

    LIMIT = 1
    SCHEMA = {
        "type": "object",
        "properties": {
            "datacenter": {"type": "string"},
            "datastore": {"type": "string"},
            "folder": {"type": "string"},
            "password": {"type": "string"},
            "respool_path": {"type": "string"},
            "user": {"type": "string"},
            "vsphere_ip": {"type": "string"},
        },
    }
    IGNORE_FIELDS = {
        "egress-subnets",
        "ingress-address",
        "private-address",
    }

    def __init__(self, charm, endpoint="vsphere-integration"):
        super().__init__(charm, f"relation-{endpoint}")
        self.charm = charm
        self.endpoint = endpoint

    @cached_property
    def relation(self):
        """The relation to the integrator, or None."""
        return self.model.get_relation(self.endpoint)

    @cached_property
    def _data(self):
        if not (self.relation and self.relation.units):
            return {}
        raw_data = self.relation.data[list(self.relation.units)[0]]
        data = {}
        for field, raw_value in raw_data.items():
            if field in self.IGNORE_FIELDS or not raw_value:
                continue
            try:
                data[field] = json.loads(raw_value)
            except json.JSONDecodeError as e:
                log.error(f"Failed to decode relation data in {field}: {e}")
        return data

    @property
    def is_ready(self):
        """Whether the request for this instance has been completed."""
        return all(
            field is not None
            for field in [
                self.vsphere_ip,
                self.user,
                self.password,
                self.datacenter,
                self.datastore,
                self.folder,
                self.respool_path,
            ]
        )

    def _value(self, key):
        if not self._data:
            return None
        return self._data.get(key)

    @property
    def datacenter(self):
        """The datacenter value."""
        return self._value("datacenter")

    @property
    def datastore(self):
        """The datastore value."""
        return self._value("datastore")

    @property
    def folder(self):
        """The folder value."""
        return self._value("folder")

    @property
    def user(self):
        """The user value."""
        return self._value("user")

    @property
    def password(self):
        """The password value."""
        return self._value("password")

    @property
    def respool_path(self):
        """The respool_path value."""
        return self._value("respool_path")

    @property
    def vsphere_ip(self):
        """The vsphere_ip value."""
        return self._value("vsphere_ip")
