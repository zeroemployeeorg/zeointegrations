# Recovered provider security follow-up

sow: zeointegrations-SOW-02. Implements Council F1-F4 and Architect ans_76c036edcf1f1881 after recovery PR33.

Bluesky accepts only the existing `https://bsky.social` origin, optionally spelled with port443 or a trailing slash. Custom PDS hosts, insecure URLs, userinfo, other ports, paths, queries and fragments are refused before credentials reach a client factory or HTTP request. Both POSTs disable redirects; redirect responses are errors. There is no opt-in override or live-provider test.

Credential filenames resolve under the provider's per-user configuration directory (`platformdirs_config_dir()/bluesky`). Absolute paths outside that directory and resolved symlink escapes are refused. Relative filenames resolve within that directory, independently of the working directory. Paths are rechecked for reads and writes. Before writing the existing atomic0600 credential file, its directory is made0700; permission failure refuses the save.

Server diagnostics include at most256 characters of provider text. Notion OAuth preserves the normalized custody error even when best-effort cleanup also fails. Failed cleanup does not imply that the already stored credential was deleted; this change makes no such claim.

All controls use offline doubles and temporary configuration directories. No installation, credential enrolment or live Bluesky use is authorized or evidenced by this patch. Gemini's Core design remains parked.
