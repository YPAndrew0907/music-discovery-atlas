# Explicit corpus releases

Reviewed release directories are selected only by the package-pinned active-corpus.json file. Disabled or absent selection retains the original108-track runtime pins. A new release must pass server/corpus_release.py with its separately approved manifest digest before runtime use. This directory contains no synthetic training or listening catalog. quarantine.json is the reviewed rights quarantine list: every corpus build drops the recordings it names (see docs/RIGHTS_QUARANTINE.md).
