# newsfeed

## Usage

If you have Newsboat installed on your system, you can run the shell script included in this repo, `legacy_launcher` to run the app.  This will setup a local cache in the project directory.

To install newsboat, you can use `sudo apt install newsboat`, or `sudo snap install newsboat`.

To run this project using docker:

`docker run -it --rm -v newsfeed:/root/.newsboat anthonyrussano/devops-newsfeeds:latest`
