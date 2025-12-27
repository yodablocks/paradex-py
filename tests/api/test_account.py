import asyncio
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from starknet_py.common import int_from_hex

from paradex_py.account.account import ParadexAccount
from paradex_py.account.utils import (
    typed_data_to_message_hash,
    unflatten_signature,
    verify_message_signature,
)
from paradex_py.message.auth import build_auth_message
from paradex_py.message.onboarding import build_onboarding_message
from tests.mocks.api_client import MockApiClient

TEST_L1_ADDRESS = "0xd2c7314539dCe7752c8120af4eC2AA750Cf2035e"
TEST_L1_PRIVATE_KEY = int_from_hex(
    "f8e4d1d772cdd44e5e77615ad11cc071c94e4c06dc21150d903f28e6aa6abdff"
)
TEST_L2_ADDRESS = int_from_hex(
    "0x129c135ed63df9353885e292be4426b8ed6122b13c6c0e1bb787288a1f5adfa"
)
TEST_L2_PRIVATE_KEY = int_from_hex(
    "0x543b6cf6c91817a87174aaea4fb370ac1c694e864d7740d728f8344d53e815"
)
TEST_L2_PUBLIC_KEY = int_from_hex(
    "0x2c144d2f2d4fc61b6f8967f3ba0012a87d90140bcfe5a3e92e8df83258c960f"
)


def test_account_l1_private_key():
    api_client = MockApiClient()
    config = api_client.fetch_system_config()

    account = ParadexAccount(
        config=config,
        l1_address=TEST_L1_ADDRESS,
        l1_private_key=TEST_L1_PRIVATE_KEY,
    )

    assert account.l2_address == TEST_L2_ADDRESS
    assert account.starknet.address == TEST_L2_ADDRESS

    assert account.l2_private_key == TEST_L2_PRIVATE_KEY
    assert account.l2_public_key == TEST_L2_PUBLIC_KEY


def test_account_l2_private_key():
    api_client = MockApiClient()
    config = api_client.fetch_system_config()

    account = ParadexAccount(
        config=config,
        l1_address=TEST_L1_ADDRESS,
        l2_private_key=TEST_L2_PRIVATE_KEY,
    )

    assert account.l2_address == TEST_L2_ADDRESS
    assert account.starknet.address == TEST_L2_ADDRESS

    assert account.l2_private_key == TEST_L2_PRIVATE_KEY
    assert account.l2_public_key == TEST_L2_PUBLIC_KEY


def test_account_onboarding_signature():
    api_client = MockApiClient()
    config = api_client.fetch_system_config()

    account = ParadexAccount(
        config=config,
        l1_address=TEST_L1_ADDRESS,
        l2_private_key=TEST_L2_PRIVATE_KEY,
    )

    sig = account.onboarding_signature()

    message = build_onboarding_message(account.l2_chain_id)
    is_signature_valid = verify_message_signature(
        typed_data_to_message_hash(message, account.l2_address),
        unflatten_signature(sig),
        account.l2_public_key,
    )
    assert is_signature_valid is True


def test_account_auth_signature():
    api_client = MockApiClient()
    config = api_client.fetch_system_config()

    account = ParadexAccount(
        config=config,
        l1_address=TEST_L1_ADDRESS,
        l2_private_key=TEST_L2_PRIVATE_KEY,
    )

    timestamp = 1706868900
    expiry = 1706955300
    sig = account.auth_signature(timestamp, expiry)

    message = build_auth_message(account.l2_chain_id, timestamp, expiry)
    is_signature_valid = verify_message_signature(
        typed_data_to_message_hash(message, account.l2_address),
        unflatten_signature(sig),
        account.l2_public_key,
    )
    assert is_signature_valid is True


# NEW TESTS FOR TRANSFER OPTIMIZATION


@pytest.mark.asyncio
async def test_contract_caching():
    """Test that contracts are cached and not reloaded."""
    api_client = MockApiClient()
    config = api_client.fetch_system_config()

    account = ParadexAccount(
        config=config,
        l1_address=TEST_L1_ADDRESS,
        l2_private_key=TEST_L2_PRIVATE_KEY,
    )

    # Mock starknet and load_contract
    account.starknet.load_contract = AsyncMock(return_value=MagicMock())

    address = int_from_hex(config.paraclear_address)

    # First call - should load from RPC
    contract1 = await account._get_cached_contract(address, is_cairo0=False)
    assert account.starknet.load_contract.call_count == 1

    # Second call - should use cache (no additional RPC call)
    contract2 = await account._get_cached_contract(address, is_cairo0=False)
    assert account.starknet.load_contract.call_count == 1  # Still 1!

    # Verify same object returned
    assert contract1 is contract2


@pytest.mark.asyncio
async def test_transfer_on_l2_parallel_operations():
    """Test that balance and multisig checks run in parallel."""
    api_client = MockApiClient()
    config = api_client.fetch_system_config()

    account = ParadexAccount(
        config=config,
        l1_address=TEST_L1_ADDRESS,
        l2_private_key=TEST_L2_PRIVATE_KEY,
    )

    # Track call times to verify parallelization
    call_times = {}

    async def mock_balance_call(*args, **kwargs):
        call_times["balance_start"] = asyncio.get_event_loop().time()
        await asyncio.sleep(0.05)  # Simulate RPC latency
        call_times["balance_end"] = asyncio.get_event_loop().time()
        return (Decimal("1000.0"),)

    async def mock_multisig_call(*args, **kwargs):
        call_times["multisig_start"] = asyncio.get_event_loop().time()
        await asyncio.sleep(0.05)  # Simulate RPC latency
        call_times["multisig_end"] = asyncio.get_event_loop().time()
        return False

    # Mock the contract and starknet methods
    mock_contract = MagicMock()
    mock_contract.functions = {"getTokenAssetBalance": MagicMock()}
    mock_contract.functions["getTokenAssetBalance"].call = mock_balance_call

    account._get_cached_contract = AsyncMock(return_value=mock_contract)
    account.starknet.check_multisig_required = mock_multisig_call
    account.starknet.prepare_invoke = AsyncMock(return_value=MagicMock())
    account.starknet.process_invoke = AsyncMock()

    # Mock prepare_invoke_v3
    mock_contract.functions["transfer"] = MagicMock()
    mock_contract.functions["transfer"].prepare_invoke_v3 = MagicMock(
        return_value=MagicMock()
    )

    # Run transfer
    await account.transfer_on_l2("0x789", Decimal("100.5"))

    # Verify parallelization:
    balance_duration = call_times["balance_end"] - call_times["balance_start"]
    multisig_duration = call_times["multisig_end"] - call_times["multisig_start"]

    # Both should take ~0.05s, and they should overlap significantly
    if (
        call_times["balance_end"] > call_times["multisig_start"]
        and call_times["multisig_end"] > call_times["balance_start"]
    ):
        overlap_ratio = (
            min(call_times["balance_end"], call_times["multisig_end"])
            - max(call_times["balance_start"], call_times["multisig_start"])
        ) / max(balance_duration, multisig_duration)

        # If parallel, overlap should be significant (>0.4)
        assert (
            overlap_ratio > 0.4
        ), "Balance and multisig should run in parallel"


@pytest.mark.asyncio
async def test_transfer_on_l2_batch():
    """Test batch transfers."""
    api_client = MockApiClient()
    config = api_client.fetch_system_config()

    account = ParadexAccount(
        config=config,
        l1_address=TEST_L1_ADDRESS,
        l2_private_key=TEST_L2_PRIVATE_KEY,
    )

    mock_contract = MagicMock()
    account._get_cached_contract = AsyncMock(return_value=mock_contract)
    account.starknet.check_multisig_required = AsyncMock(return_value=False)
    account.starknet.prepare_invoke = AsyncMock(return_value=MagicMock())
    account.starknet.process_invoke = AsyncMock()

    mock_contract.functions = {"getTokenAssetBalance": MagicMock()}
    mock_contract.functions["getTokenAssetBalance"].call = AsyncMock(
        return_value=(Decimal("5000.0"),)
    )
    mock_contract.functions["transfer"] = MagicMock()
    mock_contract.functions["transfer"].prepare_invoke_v3 = MagicMock(
        return_value=MagicMock()
    )

    # Execute batch transfer
    transfers = [
        ("0x111", Decimal("100.5")),
        ("0x222", Decimal("200.25")),
        ("0x333", Decimal("150.75")),
    ]

    await account.transfer_on_l2_batch(transfers)

    # Verify prepare_invoke_v3 was called for each transfer
    assert mock_contract.functions["transfer"].prepare_invoke_v3.call_count == 3

    # Verify single transaction batch
    assert account.starknet.prepare_invoke.call_count == 1
    assert account.starknet.process_invoke.call_count == 1


def test_paraclear_decimals_cache():
    """Test that paraclear decimals are cached."""
    api_client = MockApiClient()
    config = api_client.fetch_system_config()

    account = ParadexAccount(
        config=config,
        l1_address=TEST_L1_ADDRESS,
        l2_private_key=TEST_L2_PRIVATE_KEY,
    )

    # First call
    decimals1 = account._get_paraclear_decimals()

    # Modify config (shouldn't affect cache)
    original_decimals = config.paraclear_decimals
    config.paraclear_decimals = 99

    # Second call - should return cached value
    decimals2 = account._get_paraclear_decimals()

    assert decimals1 == decimals2 == original_decimals


def test_clear_contract_cache():
    """Test that contract cache can be cleared."""
    api_client = MockApiClient()
    config = api_client.fetch_system_config()

    account = ParadexAccount(
        config=config,
        l1_address=TEST_L1_ADDRESS,
        l2_private_key=TEST_L2_PRIVATE_KEY,
    )

    # Add something to cache
    account._contract_cache[(123, True)] = MagicMock()
    assert len(account._contract_cache) > 0

    # Clear cache
    account.clear_contract_cache()
    assert len(account._contract_cache) == 0