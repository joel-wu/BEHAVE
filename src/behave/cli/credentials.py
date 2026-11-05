"""Create a private credential file from one explicitly named environment binding.

No provider call is made. The JSON output is a public CredentialRef for existing
hosted endpoint configurations, never the credential value. Choose a destination
outside repositories, run directories and archives; its parent must already exist.
Existing files and symlinks are never replaced. Rotation uses a new destination
and an advanced deployment revision.
"""

import argparse
import os
from pathlib import Path

from behave.core.records import canonical_json


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-env", dest="key", required=True, metavar="NAME",
                        help="name of the single environment binding to import, not its value")
    parser.add_argument("--output", required=True, type=Path,
                        help="new private credential file; parent directory must exist")
    parser.add_argument("--revision", required=True, help="operator-maintained credential deployment revision")
    args = parser.parse_args(argv)
    from behave.agent.providers.credentials import CredentialRef, create_credential_file

    try:
        # Do not resolve the final symlink: it must remain an occupied destination.
        ref = CredentialRef(str(args.output.absolute()), args.key, args.revision)
        create_credential_file(ref, environment=os.environ)
    except FileExistsError:
        error = {"code": "credential-destination-exists",
                 "message": "Use a new destination; existing files are not replaced."}
    except OSError as exc:
        error = {"code": "credential-write-failed",
                 "message": os.strerror(exc.errno) if exc.errno is not None else "Private credential publication failed."}
    except (ValueError, TypeError):
        error = {"code": "credential-input-invalid",
                 "message": "Check the named environment binding, reference and literal credential format."}
    else:
        print(canonical_json(ref.as_dict()).decode())
        return 0
    # Exception strings may contain private provider or filesystem data.
    print(canonical_json({"status": "unavailable", **error}).decode())
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
