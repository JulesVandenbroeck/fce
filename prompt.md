# Feature changes prompts
Each feature prompt has a title and is taken as one merge request. Development of a feature is not started before the previous feature has been completed.

# Small bug-fixes and UI changes
Implement these bug-fixes and small UI changes:
- Add statistical uncertainty to the data points in plots
- Add a ratio pannel Data / Pred. that has the uncertainty from data. The ratio panel goes from 0 to 2 and in case zero events are in either data or prediction, set the value to 1 without uncertainty.
- Make it possible to do multiple statistical fits in case of multiple Histograms. These fits are individual for each histogram and are added to the drop down menu of the selection of the histogram.
- If in a fit the "Discovery Significane is at least 5, make a pop-up that says the process that was fitted is Discovered. add some animation if possible in DearPyGui
- In the terminal when "Processing", do not print the "[4/6]". Instead just print "Processing..."
