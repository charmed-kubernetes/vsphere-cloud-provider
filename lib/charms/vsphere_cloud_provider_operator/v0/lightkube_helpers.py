# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
"""Helpers to make working with lightkube a little easier."""
import logging

from lightkube import Client
from lightkube.core.exceptions import ApiError

# The unique Charmhub library identifier, never change it
LIBID = "55eef18966014a06b35d8e9d3346e166"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1


log = logging.getLogger(__name__)


class LightKubeHelpers:
    """Helper for interacting with Kubernetes via lightkube."""

    def __init__(self, default_namespace):
        self.client = Client(namespace=default_namespace, field_manager="lightkube")

    def apply_resources(self, resources):
        """Create or update a resource."""
        for obj in resources:
            self.client.apply(obj, namespace=obj.metadata.namespace)

    def delete_resources(
        self, resources, namespace=None, ignore_not_found=False, ignore_unauthorized=False
    ):
        """Delete a resource."""
        for obj in resources:
            self.delete_resource(
                type(obj),
                obj.metadata.name,
                namespace=namespace,
                ignore_not_found=ignore_not_found,
                ignore_unauthorized=ignore_unauthorized,
            )

    def delete_resource(
        self,
        resource_type,
        name,
        namespace=None,
        ignore_not_found=False,
        ignore_unauthorized=False,
    ):
        """Delete a resource."""
        try:
            self.client.delete(resource_type, name, namespace=namespace)
        except ApiError as err:
            log.exception("ApiError encountered while attempting to delete resource.")
            if err.status.message is not None:
                if "not found" in err.status.message and ignore_not_found:
                    log.error("Ignoring not found error:\n%s", err.status.message)
                elif "(Unauthorized)" in err.status.message and ignore_unauthorized:
                    # Ignore error from https://bugs.launchpad.net/juju/+bug/1941655
                    log.error("Ignoring unauthorized error:\n%s,", err.status.message)
                else:
                    log.error(err.status.message)
                    raise
            else:
                raise
