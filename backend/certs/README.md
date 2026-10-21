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
    [IO.File]::WriteAllText("$PWD\backend\certs\netfree-$($_.Thumbprint.Substring(0,8)).crt",
      "-----BEGIN CERTIFICATE-----`n$b64`n-----END CERTIFICATE-----`n")
  }
```

On Mac/Linux: download the certificate from https://netfree.link/cacert/ and
save it here with a `.crt` extension.

## Still `CERTIFICATE_VERIFY_FAILED` with the roots in place?

Since 2026-10, NetFree signs sites with a "NetFree Node Intermediate CA" and
sends two copies of it: one under an issuer that isn't distributed ("NetFree
Sign ,Yossi"), and one under "NetFree Intermediate CA 2026#09" -> NetFree Root
CA. OpenSSL picks the first and gives up.

The good node intermediate is reissued every ~2 days, so a copy saved here
expires quickly. Instead, set this in `.env` and rebuild:

```
NETFREE_REFRESH_CERTS=1
```

`backend/netfree-certs.sh` then fetches the current good intermediates during
the build (right before `pip install`) and on every container start. Trust
still has to end at a NetFree root from this folder, so this widens nothing.
If the container stays up for more than ~2 days, restart it
(`docker compose restart api`).
