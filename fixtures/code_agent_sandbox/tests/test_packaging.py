import re
import unittest
from pathlib import Path
from setuptools import find_packages


class PackagingTests(unittest.TestCase):
    def test_setuptools_package_discovery_includes_subpackages(self):
        pyproject_text = Path("pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'include\s*=\s*\[(.*?)\]', pyproject_text, re.DOTALL)
        self.assertIsNotNone(match)
        include = [item.strip().strip('"\'') for item in match.group(1).split(',') if item.strip()]

        self.assertIn("simple_hermes", include)
        self.assertIn("simple_hermes.*", include)

        packages = find_packages(include=include)
        self.assertIn("simple_hermes.agent", packages)
        self.assertIn("simple_hermes.tools", packages)
        self.assertIn("simple_hermes.state", packages)
