# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import unittest.mock as mock

import pytest


@pytest.fixture(autouse=True)
def lk_client():
    with mock.patch("manifests.Client") as mock_lightkube:
        yield mock_lightkube
