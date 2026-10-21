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
