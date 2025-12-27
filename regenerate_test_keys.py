#!/usr/bin/env python3
"""
Regenerate Paradex test keys from L1 private key.
Uses the actual derivation logic to get correct L2 keys.
"""

from starknet_py.common import int_from_hex
from starknet_py.net.signer.stark_curve_signer import KeyPair
from starknet_py.hash.address import compute_address
from starknet_py.hash.selector import get_selector_from_name
from paradex_py.message.stark_key import build_stark_key_message
from paradex_py.account.utils import derive_stark_key
from tests.mocks.api_client import MockApiClient

# Your existing test L1 key
TEST_L1_ADDRESS = "0xd2c7314539dCe7752c8120af4eC2AA750Cf2035e"
TEST_L1_PRIVATE_KEY = int_from_hex("f8e4d1d772cdd44e5e77615ad11cc071c94e4c06dc21150d903f28e6aa6abdff")

# Get config from mock
api_client = MockApiClient()
config = api_client.fetch_system_config()

# Derive L2 keys using current logic
stark_key_msg = build_stark_key_message(int(config.l1_chain_id))
derived_l2_private_key = derive_stark_key(TEST_L1_PRIVATE_KEY, stark_key_msg)

key_pair = KeyPair.from_private_key(derived_l2_private_key)
derived_l2_public_key = key_pair.public_key

# Compute address
calldata = [
    int_from_hex(config.paraclear_account_hash),
    get_selector_from_name("initialize"),
    2,
    derived_l2_public_key,
    0,
]
derived_l2_address = compute_address(
    class_hash=int_from_hex(config.paraclear_account_proxy_hash),
    constructor_calldata=calldata,
    salt=derived_l2_public_key,
)

print(f"TEST_L2_ADDRESS = {hex(derived_l2_address)}")
print(f"TEST_L2_PRIVATE_KEY = {hex(derived_l2_private_key)}")
print(f"TEST_L2_PUBLIC_KEY = {hex(derived_l2_public_key)}")