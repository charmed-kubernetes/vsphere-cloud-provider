# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Implementation of vsphere-integration interface.

This only implements the requires side, currently, since the integrator
is still using the Reactive Charm framework self.
"""
import json
import logging
from typing import Optional

from backports.cached_property import cached_property
from ops.charm import RelationBrokenEvent
from ops.framework import Object
from pydantic import BaseModel, Extra, Field, StrictStr, ValidationError

log = logging.getLogger(__name__)

class VsphereIntegrationData(BaseModel, extra=Extra.allow):
    datacenter: StrictStr
    datastore: Optional[StrictStr]
    folder: Optional[StrictStr]
    password: StrictStr
    respool_path: Optional[StrictStr]
    user: StrictStr
    vsphere_ip: StrictStr


class VsphereIntegrationRequires(Object):
    """Requires side of vsphere-integration relation."""

    LIMIT = 1

    def __init__(self, charm, endpoint="vsphere-integration"):
        super().__init__(charm, f"relation-{endpoint}")
        self.charm = charm
        self.endpoint = endpoint

    @cached_property
    def relation(self):
        """The relation to the integrator, or None."""
        return self.model.get_relation(self.endpoint)

    @cached_property
    def _raw_data(self):
        if self.relation and self.relation.units:
            return self.relation.data[list(self.relation.units)[0]]
        return None

    @cached_property
    def _data(self) -> Optional[VsphereIntegrationData]:
        raw = self._raw_data
        return VsphereIntegrationData(**raw) if raw else None

    def evaluate_relation(self, event) -> Optional[str]:
        """Determine if relation is ready."""
        no_relation = not self.relation or (
            isinstance(event, RelationBrokenEvent) and event.relation is self.relation
        )
        if not self.is_ready:
            if no_relation:
                return f"Missing required {self.endpoint} relation"
            return f"Waiting for {self.endpoint} relation"
        return None

    @property
    def is_ready(self):
        """Whether the request for this instance has been completed."""
        try:
            self._data
        except ValidationError as ve:
            log.error(f"{self.endpoint} relation data not yet valid. ({ve}")
            return False
        if self._data is None:
            log.error(f"{self.endpoint} relation data not yet available.")
            return False
        return True

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
