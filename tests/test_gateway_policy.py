import unittest
from pcl_codex_bridge.gateway_policy import management_networks, LOOPBACK_CIDRS


class GatewayPolicyTests(unittest.TestCase):
    def test_loopback_defaults(self):
        for host in ("127.0.0.1", "::1", "localhost"):
            self.assertEqual(len(management_networks(host, LOOPBACK_CIDRS)), 2)

    def test_remote_listener_cannot_silently_deny_every_remote_client(self):
        for host in ("100.113.234.58", "0.0.0.0", "::", "relay.example"):
            with self.assertRaisesRegex(ValueError, "explicit remote"):
                management_networks(host, LOOPBACK_CIDRS)

    def test_explicit_tailnet_policy(self):
        nets = management_networks("100.113.234.58", LOOPBACK_CIDRS + ",100.64.0.0/10")
        import ipaddress
        self.assertTrue(any(ipaddress.ip_address("100.123.137.49") in n for n in nets))
        self.assertFalse(any(ipaddress.ip_address("192.168.1.2") in n for n in nets))

    def test_empty_and_invalid_fail(self):
        for value in ("", "invalid"):
            with self.assertRaises(ValueError):
                management_networks("127.0.0.1", value)
