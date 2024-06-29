import pytest
from unittest.mock import Mock, patch
from aiosfpubsub.client import Client


@pytest.fixture
def client() -> Client:
    return Client(
        url="https://test.salesforce.com",
        username="test_user",
        password="test_password",
        grpc_host="test.grpc.host",
        grpc_port=443,
        api_version="57.0",
    )


@pytest.mark.asyncio
@patch("aiosfpubsub.client.httpx.post")
async def test_auth_success(mock_post, client: Client) -> None:
    mock_xml_content: str = b"""
        <soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/">
            <soapenv:Body>
                <loginResponse>
                    <result>
                        <serverUrl>https://instance.salesforce.com</serverUrl>
                        <sessionId>test_session_id</sessionId>
                        <userId>005x0000001S2b9</userId>
                        <userInfo>
                            <accessibilityMode>false</accessibilityMode>
                            <currencySymbol>$</currencySymbol>
                            <orgAttachmentFileSizeLimit>5242880</orgAttachmentFileSizeLimit>
                            <orgDefaultCurrencyIsoCode>USD</orgDefaultCurrencyIsoCode>
                            <orgDisallowHtmlAttachments>false</orgDisallowHtmlAttachments>
                            <orgHasPersonAccounts>false</orgHasPersonAccounts>
                            <organizationId>test_tenant_id</organizationId>
                            <organizationMultiCurrency>false</organizationMultiCurrency>
                            <organizationName>Test Org</organizationName>
                            <profileId>00ex0000000nJFY</profileId>
                            <roleId>00Ex0000000nKAx</roleId>
                            <sessionSecondsValid>7200</sessionSecondsValid>
                            <userDefaultCurrencyIsoCode>USD</userDefaultCurrencyIsoCode>
                            <userEmail>test@example.com</userEmail>
                            <userFullName>Test User</userFullName>
                            <userId>005x0000001S2b9</userId>
                            <userLanguage>en_US</userLanguage>
                            <userLocale>en_US</userLocale>
                            <userName>test@example.com</userName>
                            <userTimeZone>America/Los_Angeles</userTimeZone>
                            <userType>Standard</userType>
                            <userUiSkin>Theme3</userUiSkin>
                        </userInfo>
                    </result>
                </loginResponse>
            </soapenv:Body>
        </soapenv:Envelope>
    """
    mock_response = Mock()
    mock_response.content = mock_xml_content
    mock_post.return_value = mock_response

    client.auth()

    mock_post.assert_called_once()
    assert client.url == "instance.salesforce.com"
    assert client.session_id == "test_session_id"
    assert client.tenant_id == "test_tenant_id"
    assert client.metadata == (
        ("accesstoken", "test_session_id"),
        ("instanceurl", "instance.salesforce.com"),
        ("tenantid", "test_tenant_id"),
    )

@pytest.mark.asyncio
@patch("aiosfpubsub.client.httpx.post")
async def test_auth_failure(mock_post, client: Client) -> None:
    mock_post.side_effect = Exception("Mock HTTP Error")
    with pytest.raises(Exception):
        await client.auth()

