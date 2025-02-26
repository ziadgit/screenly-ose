# Screenly OSE - Digital Signage for the Raspberry Pi

To learn more about Screenly, please visit the official website at [ScreenlyApp.com](http://www.screenlyapp.com). On the official site, you'll find the complete installation instructions, along with a live-demo of Screenly.

## Disk Image Changelog

### 2014-07-17

 * Fixes issue with load screen failing to connect.
 * Adds support for video feeds ([#210](https://github.com/wireload/screenly-ose/issues/210)).
 * Resolves issue with assets not being added ([#209](https://github.com/wireload/screenly-ose/issues/209)).
 * Resolves issue with assets not moving to active properly ([#201](https://github.com/wireload/screenly-ose/issues/201)).
 * Pulls in APT updates from Screenly's APT repository.

### 2014-01-11

 * Upgrade kernel (3.10.25+) and firmware. Tracked in [this](https://github.com/wireload/rpi-firmware) fork.
 * Change and use Screenly's APT repository (apt.screenlyapp.com).
 * `apt-get upgrade` to the Screenly APT repository.
 * Update Screenly to latest version.
 * The disk image is available at [ScreenlyApp.com](http://www.screenlyapp.com).

## Running the Unit Tests

    nosetests --with-doctest
## Distributed Ledger Technology

* Something like but also different from a bloomberg terminal for DLT would be cool
