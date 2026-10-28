# Reference PD targets

One file per clip, in the submission format: the open-loop 60 Hz absolute joint-position targets
the oracle copies into `/home/user/submission/`. They were obtained by replaying a tracking
controller trained on the HOT3D hand and object references and converting its actions to PD
targets, so they are derivatives of HOT3D hand data: CC BY-NC-SA 4.0, non-commercial
(`LICENSE-HOT3D.txt`; HOT3D, Meta Platforms Technologies, LLC, arXiv:2406.09598).
