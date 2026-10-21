Place additional PEM-encoded **CA certificates** here with a `.crt` extension,
for example your organization's approved Netskope TLS-inspection root CA.
Use one certificate per file. Do not place private keys here.

Both Dockerfiles bootstrap `apk` trust from these files before installing packages,
then run `update-ca-certificates` for runtime Newsboat/elinks HTTPS requests.
Rebuild the images after adding or rotating a CA. TLS verification remains enabled.

`*.crt` and `*.pem` files in this directory are ignored by Git. Container builds
include only `.crt` files. Python's static generator accepts either format via
`--ca-bundle PATH`; it also trusts the host's system CA store.

Images built with these files contain your corporate trust roots. Keep those
images local/private. The repository's public CI builds have no local CA files.
