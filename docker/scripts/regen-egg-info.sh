#!/bin/bash
# Regenerate the local package's .dist-info so Python can import it as a
# namespace package. Needed because the builder deleted RECORD files to
# save space, but pip uses RECORD to detect existing installations.
set -e

DIST_DIR="/usr/local/lib/python3.12/dist-packages/openaudible_to_audiobookshelf-1.0.0.dist-info"
mkdir -p "$DIST_DIR"

# Create top_level.txt -- tells pkg_resources what the top-level package is
cat > "$DIST_DIR/top_level.txt" <<EOF
openaudible_to_audiobookshelf
EOF

# Create minimal METADATA -- required for importlib.metadata.distribution()
cat > "$DIST_DIR/METADATA" <<EOF
Metadata-Version: 2.1
Name: openaudible-to-audiobookshelf
Version: 1.0.0
EOF

# Create a RECORD that doesn't actually claim any files (so pip won't try to manage them)
cat > "$DIST_DIR/RECORD" <<EOF
openaudible_to_audiobookshelf-1.0.0.dist-info/top_level.txt,sha256=placeholder,0
openaudible_to_audiobookshelf-1.0.0.dist-info/METADATA,sha256=placeholder,0
openaudible_to_audiobookshelf-1.0.0.dist-info/RECORD,,
EOF

echo "Regenerated $DIST_DIR"