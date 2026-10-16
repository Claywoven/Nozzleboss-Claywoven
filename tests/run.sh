#!/usr/bin/env bash
# Run every tests/test_*.py headless in Blender. BLENDER=/path/to/blender
# overrides the binary. Exits non-zero if any test fails.
cd "$(dirname "$0")/.." || exit 1
blender=${BLENDER:-blender}
status=0
for test in tests/test_*.py; do
    if out=$("$blender" --background --factory-startup --python-exit-code 1 --python "$test" 2>&1); then
        if grep -q "^SKIP" <<<"$out"; then echo "SKIP $test"; else echo "PASS $test"; fi
    else
        echo "FAIL $test"
        tail -n 30 <<<"$out"
        status=1
    fi
done
exit $status
