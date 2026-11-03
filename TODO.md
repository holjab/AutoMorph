# Dev Branch Tasks & Roadmap

## Immediate Fixes

- [ ] Dependencies give ugly error
    - [x] Need to add a check for .env and .ini
    - [x] Need to add protoc sudo apt install protobuf-compiler / json for obtanium
    - [x] Look into using pyaxmlparser instead of aapt

- [ ] only write options checksum on succesfull patch - or make a json of sucessfully patched apps and it can read from that instead
    - [x] patched.json
    - [ ] check patched.json

- [ ] App search issues
    - [x] Chess.com - has-googleplay
    - [x] Accuweather - has -rc
            - invalid app response form gplay 19300002
    - [ ] Facebook had two versions on apkpure  475019281, 475019367 apkmirror and apkcombo found 367
    - [ ] Instagram 3 variants  384510792 | 384510800 | 384510833 | chose 792

- [ ] Needs to check if a patched verison actually exists before exiting with no patches applied

## Enhancements
- [ ] Add support for unreleased patches

- [ ] Only check number of patched packages for packages that have been patched

## Past Fixes
- [x] Check line 130 in main - version is 0.0.0 for package with no requirement
    - [x] Also an issue in line 138 as if the version is none, it doesn't find the already patched app (8.3.2 for infinity)

- [x] Improve patcher logic
    - [x] If there is only one version and only one repo, we do not need to attempt the run 2 as nothing will have changed
    - [x] Improve print statements for re-trys: if there's a retry, it can say if we're moving to a different run or trying all patches on the same run

- [x] Program crashed if it cannot find an apk rather than skipping and attempting the next package

- [x] Github not working for infinityplus
    - [x] Look into R2 error for apkcombo
    - [x] Inifinity didn't find the git link - didn't attempt

- [x] Fix falied patches and other errors in dispatcher
    - [x] Update Morphe.patch() to return a 3-tuple: (success: bool, dest: Path | str | None, reason: str | None).
    - [x] Fix Gmail 102KB clipping by streamlining patch chips, removing inlined raw result.json, and optimizing CTA buttons.

- [x] Fix Html scrappers
    - [x] See if we can find a fix for apkpure html scrapping
        - [x] Can maybe replace the protobuf method with the json method used by obtanium
        - [x] uptodown

- [x] CamScanner downloaded the older version
    - Looks as though it found that the newer version was already patched - skipped it and so fell to backup, rather than skipping completly

- [x] Wavelet bug: It's looking for 26.5 and not 26.05

- [x] Infinity is not downloading from github

- [x] Fix for pipeline 
    - [x] Instead of global repos, everything is in the override repos
    - [x] Options are only created for each repo per app rather than all - needs to check for cached repos before downloading a new one if needed

- [x] Console doesn't stop on web

- [x] Time is wrong on web and logs

## Past Enhancements
- [x] Make a soure YAML for apps that are not on any of the app stores
    - [x] Move github check logic to apkfetcher
- [x] Add overide/ignore repo logic
    - [x] Patcher
    - [x] Maybe can do sooner so that un-necassry repos are not carried through the whole program
- [x] make first run not use force if only one repo is being used. 

- [x] Make low spec mode

- [x] Add git download logic

- [x] Make downloader try apkcombo before apkpure incase of gplay dl issue

- [x] Tailscale download link logic & email

- [x] Restructure config.yaml to remove lists

- [x] Allow overide repos to take a list

- [x] Move to goopdl and remove apkeep?

- [x] Make "find specific app" in apkeep check other sources (apkfetcher?)

- [x] dockerise

- [x] Mpve to cloudflare tunnel/quick tunnel or duckDNS and caddy / pangolin

- [x] Add force flag to force patching of all apps

- [x] Improve printing for check source file in apkfetcher

- [x] Move locations from config and into compose

- [x] Delete old logs/apps
    - [x] Option to choose how many to keep

- [x] Patcher enhancements
    - [x] Add strip libs
    - [x] Key backups  



