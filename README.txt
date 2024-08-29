To run locally:

    Ensure .env has the correct endpoints
    
    start virtual enviroment in vscode:
        source venv/bin/activate

    Run app:
        python main.py

    Run Tunneling:
        ngrok http 5000

    Make sure urls are correct for app slash commands(https://api.slack.com/apps/A07JJDNEYC8/slash-commands?saved=1). 
        ngork link will be something like https://02bc-2001-bb6-31b-ff00-1567-53-8d53-adfc.ngrok-free.app/XXXXXXXXXX 

__________________________________________________________________________________________________________________________________________________________

Url for slash commands when running on google cloud
    https://paylagh-pnkhnryifa-ew.a.run.app/XXXXXXXXXX

URl for event subscriptions found here(https://api.slack.com/apps/A07JJDNEYC8/event-subscriptions?)
    https://paylagh-pnkhnryifa-ew.a.run.app/slack/events