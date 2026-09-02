**To:** tom@datapress.com (cc Datastore@london.gov.uk, same as last time)
**Subject:** Re: Technical issue with Planning London Datahub API access — follow-up

Hi Tom,

Thanks for the pointer to the docs back in August. I've gone through them and hit a dead end I wanted to flag directly rather than guess around.

I queried the dataset's own metadata endpoint:

`https://data.london.gov.uk/api/v3/dataset/planning-london-datahub-applications-236qk`

Both `resources` and `links` come back empty — no CSV, no JSON feed, nothing to query. The only thing published against this dataset appears to be the embedded Power BI dashboard. My old integration point (`planningdata.london.gov.uk/api/applications`) now redirects to a login page rather than serving data.

So my actual question is simpler than last time: **is there any resource attached to this dataset that returns individual application records programmatically** (address, description, application type, date filed), or has the Planning London Datahub's raw data feed been retired in favour of the Power BI dashboard only?

If there is a resource ID I should be using, that's all I need. If the raw feed genuinely isn't available anymore, that's a useful answer too — I can plan around it either way, I'd just rather know for certain than keep guessing at deprecated endpoints.

Thanks again for your help,
Nick
