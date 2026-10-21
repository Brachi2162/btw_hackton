# Extra root certificates (optional)

Behind a TLS-intercepting filter (e.g. NetFree), the container can't reach
PyPI or the Anthropic API unless it trusts the filter's root CA.

Put the CA as a PEM file named `*.crt` in this folder and rebuild
(`docker compose build`). Files here are git-ignored; only this README is
committed. Without a filter, leave the folder empty.

Export NetFree's roots on Windows (PowerShell, from the repo root):

```powershell
Get-ChildItem Cert:\LocalMachine\Root, Cert:\CurrentUser\Root |
  Where-Object { $_.Subject -match 'O=NetFree' -and $_.NotAfter -gt (Get-Date) } |
  Sort-Object Thumbprint -Unique | ForEach-Object {
    $b64 = [Convert]::ToBase64String($_.RawData, 'InsertLineBreaks').Replace("`r`n", "`n")
    [IO.File]::WriteAllText("backend\certs\netfree-$($_.Thumbprint.Substring(0,8)).crt",
      "-----BEGIN CERTIFICATE-----`n$b64`n-----END CERTIFICATE-----`n")
  }
```

On Mac/Linux: download the certificate from https://netfree.link/cacert/ and
save it here with a `.crt` extension.

## Still `CERTIFICATE_VERIFY_FAILED` with the roots in place?

Since 2026-10, NetFree signs sites with a "NetFree Node Intermediate CA" and
sends two copies of it: one under an issuer that isn't distributed ("NetFree
Sign ,Yossi"), and one under "NetFree Intermediate CA 2026#09" -> NetFree Root
CA. OpenSSL picks the first and gives up. Add the good intermediates as well
(trust still has to end at a NetFree root, so this widens nothing):

```bash
echo | openssl s_client -connect pypi.org:443 -servername pypi.org -showcerts 2>/dev/null \
  | awk '/BEGIN CERT/{n++} n>0{print > ("chain" n ".pem")}'
openssl x509 -in chain3.pem -noout -issuer   # expect: NetFree Intermediate CA 2026#09
cp chain3.pem backend/certs/netfree-node-intermediate.crt
cp chain4.pem backend/certs/netfree-intermediate-2026-09.crt
```

Check the issuers first: the order of the chain may differ. Then rebuild.
