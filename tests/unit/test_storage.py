from data.storage import delta_storage_options


def test_delta_storage_options_use_delta_rs_aws_keys():
    opts = delta_storage_options(
        endpoint="https://s3.eu-central-003.backblazeb2.com",
        access_key="key-id",
        secret_key="app-key",
        region="eu-central-003",
    )

    assert opts["AWS_ENDPOINT_URL"] == "https://s3.eu-central-003.backblazeb2.com"
    assert opts["AWS_ACCESS_KEY_ID"] == "key-id"
    assert opts["AWS_SECRET_ACCESS_KEY"] == "app-key"
    assert opts["AWS_REGION"] == "eu-central-003"
    assert "endpoint_url" not in opts
    assert "aws_access_key_id" not in opts
