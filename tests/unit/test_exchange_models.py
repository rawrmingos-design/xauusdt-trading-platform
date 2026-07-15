"""Tests for Bitget exchange models."""

from xauusdt.exchange.models import BitgetApiResponse, ContractInfo, to_contract


class TestBitgetApiResponse:
    def test_minimal(self) -> None:
        resp = BitgetApiResponse(code="00000", msg="success")
        assert resp.code == "00000"
        assert resp.data is None

    def test_with_data(self) -> None:
        resp = BitgetApiResponse(code="00000", msg="ok", data=[{"symbol": "BTCUSDT"}])
        assert resp.data == [{"symbol": "BTCUSDT"}]


class TestContractInfo:
    def test_minimal(self) -> None:
        info = ContractInfo(symbol="BTCUSDT_UMCBL")
        assert info.symbol == "BTCUSDT_UMCBL"

    def test_full(self) -> None:
        info = ContractInfo(
            symbol="XAU-USDT-SWAP",
            productType="UMCBL",
            baseCoin="XAU",
            quoteCoin="USDT",
            size="0.1",
            minTradeAmount="0.001",
            pricePlace="1",
            volumePlace="3",
        )
        assert info.base_coin == "XAU"
        assert info.contract_size == 0.1
        assert info.price_place == "1"

    def test_contract_size_zero_when_empty(self) -> None:
        info = ContractInfo(symbol="BTCUSDT_UMCBL")
        assert info.contract_size == 0.0


class TestToContract:
    def test_conversion(self) -> None:
        info = ContractInfo(
            symbol="XAU-USDT-SWAP",
            productType="UMCBL",
            baseCoin="XAU",
            quoteCoin="USDT",
            size="0.1",
            minTradeAmount="0.001",
            pricePlace="1",
            volumePlace="3",
        )
        contract = to_contract(info)
        assert contract.symbol == "XAU-USDT-SWAP"
        assert contract.base_coin == "XAU"
        assert contract.contract_size == 0.1
        assert contract.min_trade_amount == 0.001
        assert contract.price_precision == 1
        assert contract.volume_precision == 3

    def test_empty_fields_default_to_zero(self) -> None:
        info = ContractInfo(symbol="BTCUSDT_UMCBL")
        contract = to_contract(info)
        assert contract.contract_size == 0.0
        assert contract.min_trade_amount == 0.0
        assert contract.price_precision == 0
        assert contract.volume_precision == 0
