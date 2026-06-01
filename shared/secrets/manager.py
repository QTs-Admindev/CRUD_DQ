import boto3

_cache: dict[str, str] = {}


def get_secret(name: str) -> str:
    if name in _cache:
        return _cache[name]
    client = boto3.client("secretsmanager", region_name="us-west-1")
    response = client.get_secret_value(SecretId=name)
    value = response.get("SecretString") or response["SecretBinary"].decode("utf-8")
    _cache[name] = value
    return value
