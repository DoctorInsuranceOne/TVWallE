# Sound

The TV dashboard can play a live call out loud (on a TV whose "Can listen to calls" switch is on in the CRM), so the kiosk sends its sound to the screen.

- **Out of the screen only.** Sound goes out through the display output (HDMI or DisplayPort; a DisplayPort-to-HDMI cable carries it too) to the TV's speakers. The PC's own speaker, headphone jack, line out and S/PDIF are switched off, so the PC under the TV never makes a sound. With the TV off or unplugged, nothing plays anywhere.
- **At full volume.** The PC sends full volume; the TV's remote sets how loud. (WirePlumber's own default for a new output is 40%.)

Firefox plays through PipeWire in the kiosk user's session, and WirePlumber picks the output. The rules are in `/etc/wireplumber/wireplumber.conf.d/50-tvwalle.conf` (in the repo: `rootfs/etc/wireplumber/wireplumber.conf.d/`): HDMI/DisplayPort outputs get priority 2500 (an analog output has about 1000), analog and S/PDIF outputs are disabled, and new outputs start at volume 1.0. When the setup installs a changed rules file, it clears the volumes WirePlumber remembered for the kiosk and restarts it, so the rules apply at once.

## Checking it

On the kiosk (or over SSH):

```bash
sudo tvwalle-sound
```

It shows the sound cards, which screen is on each display output and whether it takes sound (`monitor_present 1`, with the screen's model name), the outputs with the one in use marked `*` and its volume, and each card's profiles. Someone next to the TV can also run:

```bash
sudo tvwalle-sound tones
```

which plays a 2-second tone on each output in turn and names it.

What a working kiosk shows: the screen's model on a display output (`monitor_present 1`), and `* … Digital Stereo (HDMI) [vol: 1.00]` among the outputs. Firefox's streams go to that output.

## If there's no sound

- **No `monitor_present 1` line:** the screen doesn't report sound on that input (some monitors have no speakers), or the cable or adapter doesn't carry it. An active DisplayPort-to-HDMI adapter that strips audio does this.
- **The screen is there but the output in use is "Dummy Output":** the screen's output wasn't created; look at the HDMI card's profiles (`output:hdmi-stereo` should be `*` and available).
- **Everything looks right but it's silent:** the TV's own volume or mute, or the TV set to send its sound elsewhere (a soundbar, optical out).
- **On the TV page itself,** sound plays only on a TV whose "Can listen to calls" switch is on, and only after a click on a call.

Tested on the Dell OptiPlex 9020 (Intel HD 4600, DisplayPort only, through a DisplayPort-to-HDMI cable): its display audio is a separate sound card ("HDA Intel HDMI"), and without these rules Linux sent the sound to the PC's built-in speaker (ALC3234) at 40%.
