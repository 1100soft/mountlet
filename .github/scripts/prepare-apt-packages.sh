#!/usr/bin/env bash
set -euo pipefail

if [ "$#" -ne 5 ]; then
  echo "usage: $0 STANDARD_DEB LEAN_DEB OUTPUT_DIR stable|preview RELEASE_ID" >&2
  exit 2
fi

standard_deb=$1
lean_deb=$2
output_dir=$3
channel=$4
release_id=$5

case "$channel" in stable|preview) ;; *) echo "invalid channel: $channel" >&2; exit 2 ;; esac
for package in "$standard_deb" "$lean_deb"; do
  dpkg-deb --info "$package" >/dev/null
  test "$(dpkg-deb --field "$package" Package)" = mountlet
  test "$(dpkg-deb --field "$package" Architecture)" = amd64
done

app_version=$(dpkg-deb --field "$standard_deb" Version)
test "$(dpkg-deb --field "$lean_deb" Version)" = "$app_version"
mkdir -p "$output_dir"

repack() {
  local source=$1 package_name=$2 version=$3 needs_rclone=$4 destination=$5
  local package_root dependencies
  package_root=$(mktemp -d)
  dpkg-deb --raw-extract "$source" "$package_root"
  sed -i \
    -e "s/^Package: .*/Package: ${package_name}/" \
    -e "s/^Version: .*/Version: ${version}/" \
    -e '/^Conflicts: /d' -e '/^Replaces: /d' -e '/^Provides: /d' \
    "$package_root/DEBIAN/control"
  local alternatives=() candidate relationships
  for candidate in mountlet mountlet-lean mountlet-preview mountlet-lean-preview; do
    [ "$candidate" = "$package_name" ] || alternatives+=("$candidate")
  done
  relationships=$(IFS=', '; echo "${alternatives[*]}")
  printf 'Conflicts: %s\n' "$relationships" >> "$package_root/DEBIAN/control"
  printf 'Replaces: %s\n' "$relationships" >> "$package_root/DEBIAN/control"
  printf 'Provides: mountlet\n' >> "$package_root/DEBIAN/control"
  if [ "$needs_rclone" = true ]; then
    dependencies=$(dpkg-deb --field "$source" Depends 2>/dev/null || true)
    if ! grep -Eq '(^|,)[[:space:]]*rclone([[:space:]]|,|$)' <<<"$dependencies"; then
      sed -i "s/^Depends: /Depends: rclone, /" "$package_root/DEBIAN/control"
    fi
  fi
  dpkg-deb --build --root-owner-group "$package_root" "$destination" >/dev/null
  rm -rf "$package_root"
}

if [ "$channel" = stable ]; then
  test "$app_version" = "${release_id#v}"
  cp "$standard_deb" "$output_dir/mountlet_${app_version}_amd64.deb"
  repack "$lean_deb" mountlet-lean "$app_version" true \
    "$output_dir/mountlet-lean_${app_version}_amd64.deb"
else
  version="${app_version}~preview.${release_id}"
  repack "$standard_deb" mountlet-preview "$version" false \
    "$output_dir/mountlet-preview_${version}_amd64.deb"
  repack "$lean_deb" mountlet-lean-preview "$version" true \
    "$output_dir/mountlet-lean-preview_${version}_amd64.deb"
fi

for package in "$output_dir"/*.deb; do
  dpkg-deb --info "$package" >/dev/null
  test "$(dpkg-deb --field "$package" Architecture)" = amd64
done
