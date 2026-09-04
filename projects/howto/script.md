# How To Use vidsmith And How To Buy It

## What you need first
[visual: developer terminal window on a laptop screen]
Two things, and only one of them is unusual. You need Python, which you probably already have, and you need ffmpeg, which is the only dependency that does not come from pip. On Windows that is one winget command, on a Mac it is brew, and on Debian it is apt.

## Getting it
[visual: hands typing commands on a mechanical keyboard]
Clone the repository, make a virtual environment, and install the requirements. Nothing here asks for an account, a key or a card. The demo project that ships with it renders start to finish without any of those, so you can see the output before deciding whether you want it.

## Writing the script
[diagram: a markdown file becoming scenes]
A project is a markdown file. A heading starts a scene, and so does a blank line between paragraphs, which is the one rule that catches people out. Under each heading you write the words you want spoken, and a bracketed visual line tells it what footage to go and find.

## Running the build
[visual: close up of code scrolling on a monitor at night]
One command builds the whole thing. It speaks every scene, searches for footage, and cuts the picture at the place where you land a full stop.

## While it runs
[visual: person drinking coffee while waiting at a desk]
It draws a diagram for the scenes that cannot be filmed, mixes music under the voice, and encodes the master. A minute or so of video takes a few minutes to make.

## What you get back
[visual: video editing timeline on a screen]
An mp4 you can upload, a subtitle file, a thumbnail, and a draft title, description and chapter list. It also writes the credits for every clip it used, because the stock library requires you to name the creator and link back when you pull footage through its interface.

## Two shapes from one script
[visual: phone and laptop side by side on a desk]
Ask for the vertical cut and you get a second video from the same words. The narration is reused, so the two are timed identically, and the captions reflow to the narrower frame rather than running off the edge of it.

## What it costs
[diagram: one payment against a monthly subscription]
Free for personal use, study, hobby projects and non profits. If you are making money with it, that is a commercial licence: forty nine dollars once for one person, or two hundred and ninety nine once for a company. Both are perpetual, so there is no renewal and nothing to cancel.

## How to buy it
[visual: person entering card details on a laptop]
Checkout is on Polar and the licence arrives by email, naming whoever you give at the till. Agency and client work is quoted instead, because that range is too wide for a fixed number, so describe what you do and you will get one back.

## The first thing to change after buying
[visual: studio microphone on a desk in soft light]
The default narration voice is a free endpoint that is not cleared for commercial use, so a paying user switches it to Amazon Polly before shipping anything. It is a few lines of configuration, the timings work the same way, and the reason it matters is written up in the commercial notes.

## Where to start
[visual: laptop open on a wooden desk in morning light]
Read the source, run the demo, and buy a licence only once it starts making you money.
