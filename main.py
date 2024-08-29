import slack
import os
from pathlib import Path
from dotenv import load_dotenv
from flask import Flask, request, Response
from slackeventsapi import SlackEventAdapter
from typing import List, Dict
import pandas as pd
from dataclasses import dataclass

try:
    # Attempt to get the terminal width
    screen_width = os.get_terminal_size().columns
except OSError:
    # Fallback to a default width if the above fails
    screen_width = 80

# Load the env variables (see below in flask setup and get_spreadsheet_details function)
env_path = Path(".") / ".env"
load_dotenv(dotenv_path=env_path)

# flask setup
app = Flask(__name__)
slack_event_adapter = SlackEventAdapter(os.environ["SIGNING_SECRET"], '/slack/events', app)
client = slack.WebClient(token=os.environ["SLACK_TOKEN"])

# use this to identify user vs channels messages
BOT_ID = client.api_call("auth.test")["user_id"]

# team name
TEAM = "Ranelagh"

# TREASURERS, usually gets different output for the basic command "/owe"
TREASURERS = ["Noah Coyne-Tyrrell", "Louis Stewart", "Daniel Collins"]
TREASURER = "Louis Stewart"

# payment details
BANK_IBAN = "DE02100110012006085005"
BANK_BIC = "NTSBDEB1XXX"


# required for pinging players wrt sending money, all of these are global objects! TODO: please refactor this
PING_EXCLUDED_LIST = []
PING_TIMES_PER_PLAYER = {TREASURER: "next Monday at 14:00"}
PING_LIST = []
REMINDERS = {}
DEFAULT_TIME = "next Monday at 15:00"


@dataclass
class Person:
    name: str
    current_balance: float
    total_debit: float
    total_paid: float


@dataclass
class Reminder:
    name: str
    text: str
    id: str
    recurrence: str


@slack_event_adapter.on("message")
def message(payload):
    event = payload.get("event", {})
    channel_id = event.get("channel")
    user_id = event.get("user")
    text = event.get("text")
    if BOT_ID != user_id:
        client.chat_postMessage(channel=channel_id, text=text)


def get_spreadsheet_details() -> pd.DataFrame:
    """
    Make a request to gather data from the available spreadsheet containing the people and money owed to the club.
    Make sure env variables SPREADSHEET_ID, SHEET_NAME are set!

    CSV format expected to work with:
    Required columns in this order:
     - Name : player name
     - Balance : balance they have right now
     - Total Credit : money paid in advance
     - Total Debit : money paid this year

    :return: Pandas Dataframe with the CSV format
    """
    spreadsheet_id = os.environ["SPREADSHEET_ID"]
    sheet_name = os.environ["SHEET_NAME"]
    url = f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv&gid={sheet_name}"
    data = pd.read_csv(url)
    print(data)
    return data


def process_details(name: str, data: pd.DataFrame) -> Person:
    """
    Turn CSV data into a Person object.
    :param name: name of the current person
    :param data: CSV file with format specified in get_spreadsheet_details function
    :return: Person object with parsed information
    """
    # expected default columns
    name_col = data.keys()[0]
    balance_col = data.keys()[1]
    total_col = data.keys()[2]
    paid_col = data.keys()[3]

    # get the values from CSV columns
    person_data = data.loc[data[name_col] == name]

    if person_data.empty:
        # Handle the case where the person's data is not found
        print(f"Person '{name}' not found in the data.")
        return Person(name, 0.0, 0.0, 0.0)

    print(person_data, "TEST")

    # Ensure there are enough rows before accessing them
    if len(person_data) > 0:
        current_balance = person_data[balance_col].iloc[0]
        debit = person_data[total_col].iloc[0]
        paid = person_data[paid_col].iloc[0]
    else:
        # Handle cases where no data is available
        print(f"No balance data found for {name}.")
        return Person(name, 0.0, 0.0, 0.0)

    # build the person object
    person = Person(
        name,
        float(current_balance.replace("€", "").replace(",", "")),
        float(debit.replace("€", "").replace(",", "")),
        float(paid.replace("€", "").replace(",", ""))
    )
    return person



def get_people_data() -> List[Person]:
    """
    Build List of Person from CSV file

    :return: List of Person objects for every entry in the spreadsheet
    """
    spreadsheet_data = get_spreadsheet_details()[:-3]
    peoples = []
    for person in spreadsheet_data[spreadsheet_data.keys()[0]]:
        peoples.append(process_details(person, spreadsheet_data))
    return peoples


def get_all_debtors(people: List[Person]) -> List[Person]:
    """
    Filter people with negative balances, i.e debtors

    :param people: List of people to check for debt
    :return: List of Person that match filter
    """
    debtors = []
    for person in people:
        if person.current_balance < 0:
            debtors.append(person)
    return debtors


def get_all_prepaid(people: List[Person]) -> List[Person]:
    """
    Filter people who paid the club in advance for future expenses
    :param people: List of people to apply filter to
    :return: List of Person that match filter
    """
    prepaid = []
    for person in people:
        if person.current_balance > 0:
            prepaid.append(person)
    return prepaid


def send_message_to_slack(user_id, channel_name, channel_id, text):
    """
    Slac function to post reply from bot based on where the command was issued (private chat vs channel)

    :param user_id: User to reply to
    :param channel_name: Channel type - direct message or otherwise
    :param channel_id: Channel to reply to
    :param text: What to reply with
    :return: Nothing
    """
    if channel_name == "directmessage":
        print("direct conversation")
        client.chat_postMessage(channel=user_id, text=text)
    else:
        client.chat_postMessage(channel=channel_id, text=text)


@app.route("/owe", methods=["POST"])
def owe():
    """
    Business logic for replying to people with amount owed to the club

    :return: Response Success required by the slack API
    """

    # get data from teh http request
    data = request.form

    # identify user, channel, user real name from fields
    user_id = data.get("user_id")
    channel_id = data.get("channel_id")
    user = client.users_info(user=user_id)
    user_real_name = user.get("user").get("real_name")

    # message to reply with
    response = ""

    # used to reply with default text when things go wrong
    flagged = True

    # if no hits user doesn't owe any money but no errors detected.
    hits = 0

    # used to build the amount people owe message
    breakdown = ""

    # get data about people
    people = get_people_data()

    # get debtors and prepaid (strictly positive or negative balances not 0)
    debtors = get_all_debtors(people)
    prepaid = get_all_prepaid(people)

    if user_real_name in TREASURERS:
        # If the command was issues by the TREASURERS, reply with all debtors and prepaid accounts
        flagged = False
        for user in debtors:
            response += f"*{user.name}* needs to pay *{abs(user.current_balance)}* Euros\n"
        response += "\n"
        for user in prepaid:
            response += f"*{user.name}* paid *{user.current_balance}* Euros in advance\n"
    else:
        # Find the user in the debtor/prepaid groups and build the appropriate message.
        debt = [user for user in debtors if user.name == user_real_name]
        prepaid = [user for user in prepaid if user.name == user_real_name]
        if debt:
            user = debt[0]
            breakdown = f"*{user.name}* needs to pay *{abs(user.current_balance)}* Euros\n"
            flagged = False
        elif prepaid:
            user = prepaid[0]
            breakdown = f"*{user.name}* paid *{user.current_balance}* Euros in advance\n"
            flagged = False

    response += breakdown
    response += "Use the _/paywhere_ command to find out how to pay."

    if flagged:
        if hits == 0:
            response = f"The user *{user_real_name}* owes {TEAM} *no* money."
        else:
            response = f"The search you performed went wrong, please contact {TREASURERS} for troubleshooting"

    channel_name = data.get("channel_name")

    # reply to the user
    send_message_to_slack(user_id, channel_name, channel_id, response)

    return Response(), 200

# ###################### Basic Commands ###############################################################################

@app.route("/paywhere", methods=["POST"])
def pay_where():
    """
    Command used to tell people how to pay the club

    :return: Response 200 required by slack API
    """

    # get data from request
    data = request.form
    # get user and channel for the message
    user_id = data.get("user_id")
    channel_id = data.get("channel_id")

    # payment information
    text = f"""
    Its actually working!Crazy
    Pay {TEAM} here:
    IBAN: {BANK_IBAN}
    BIC: {BANK_BIC}
    """
    channel_name = data.get("channel_name")
    print("TEST reached here")
    # reply with the message
    send_message_to_slack(user_id, channel_name, channel_id, text)
    return Response(), 200

@app.route("/commands", methods=["POST"])
def commands():
    """
    explains all the commands

    :return: Response 200 required by slack API
    """

    # get data from request
    data = request.form
    # get user and channel for the message
    user_id = data.get("user_id")
    channel_id = data.get("channel_id")

    # payment information
    text = f"""
    
    /commands:
    Displays list of commands and what they do

    /owe:
    What is my balance?

    /paywhere:
    What are the bank details?
    """
    channel_name = data.get("channel_name")

    # reply with the message
    send_message_to_slack(user_id, channel_name, channel_id, text)
    return Response(), 200


@app.route("/breakdown", methods=["POST"])
def player_finance_breakdown():
    """
    Build a record of payments from a player

    :return: Response 200 required by slack API
    """

    # get data from request
    data = request.form

    # identify user, channel, user real name from fields
    user_id = data.get("user_id")
    channel_id = data.get("channel_id")
    user = client.users_info(user=user_id)
    user_real_name = user.get("user").get("real_name")

    text = ""
    people = get_people_data()
    user = [user for user in people if user.name == user_real_name]
    if not user:
        # we can't find a slack account for this user name found in the spreadsheet!
        text += f"User {user_real_name} could not be found in {TEAM}'s financial book, " \
                f"please contact *{TREASURERS}* for further details."
    else:
        user = user[0]
        # build the financial record message
        text += f"User *{user.name}*'s breakdown is as follows:\n"

        # membership paid or not
        if user.membership > 0:
            text += f"- Membership for this year paid with the following amount: *{user.membership}* Euros\n"
        else:
            text += f"- Membership for the current year *not* paid, \n" \
                    f"      _contact {TREASURERS} if you want to become a member!_\n"

        # current balance positive, negative or 0
        if user.current_balance < 0:
            text += f"- Current balance is negative: *{user.current_balance}* Euros need to be paid. \n" \
                    f"      _Please use /paywhere command to find out how to pay_\n"
            pay_paid = "Had to pay"
        elif user.current_balance > 0:
            text += f"- Current balance is positive in excess of: *{user.current_balance}* Euros\n"
            pay_paid = "Paid"
        else:
            text += f"- Current balance is *0* Euros.\n"
            pay_paid = "Paid"

        # total amount paid so far
        if user.total_paid > 0:
            text += f"- You have paid {TEAM} *{user.total_paid}* Euros so far.\n"
        text += "\n"

        # user has paid for anything else apart from membership
        if user.payments_breakdown:
            text += f"*_Detailed breakdown of costs_*:\n"
            for event, amount in user.payments_breakdown.items():
                text += f"  - {pay_paid} {abs(amount)} Euros for \"{event}\".\n"
            if user.membership > 0:
                text += f"  - {pay_paid} {abs(user.membership)} Euros for \"Membership\".\n"

    channel_name = data.get("channel_name")
    # send the message
    send_message_to_slack(user_id, channel_name, channel_id, text)

    return Response(), 200

# ###################### Basic Commands END ############################################################################


def build_reminders():
    """
    Function to build a list of reminders

    :return: List of Reminder objects

    TODO: Refactor this to stop using global objects like PING_LIST
    """
    reminders = []
    for debtor, person in PING_LIST:
        text = f"You owe {TEAM} {debtor.current_balance} Euros. \n" \
               f"Please use /paywhere to pay or contact {TREASURERS}"
        if not PING_TIMES_PER_PLAYER.get(debtor.name):
            # add a default time when they get reminded if a more specific one wasn't set
            PING_TIMES_PER_PLAYER[debtor.name] = DEFAULT_TIME
        recurrence = PING_TIMES_PER_PLAYER[debtor.name]
        reminder = Reminder(name=debtor.name, text=text, id=person["id"], recurrence=recurrence)
        reminders.append(reminder)
    return reminders

# ###################### ADVANCED Commands  ############################################################################


@app.route("/ping", methods=["POST"])
def ping_players():
    """
    Command to set reminders for people from TREASURERS with how much they need to pay
    Take one parameter: on/off

    :return: Response 200 required by Slack API
    """

    # get data from request
    data = request.form

    # get user info
    user_id = data.get("user_id")
    channel_id = data.get("channel_id")
    toggle = data.get("text")
    user = client.users_info(user=user_id)
    user_real_name = user.get("user").get("real_name")
    channel_name = data.get("channel_name")

    players_being_reminded = []
    players_already_reminded = []
    players_unreachable = []

    text = f""

    #this command is publicly available but don't do anything if the person using it is not the TREASURERS!
    if user_real_name in TREASURERS:
        if toggle == "on":
            # we're setting up the reminders
            people = get_people_data()
            debtors = get_all_debtors(people)
            slack_people = client.users_list().get("members")

            # correlate debtors to slack names and add to global list PING_LIST
            for person in debtors:
                found = False
                for slack_person in slack_people:
                    if slack_person.get("real_name") == person.name:
                        PING_LIST.append((person, slack_person))
                        found = True
                        break
                if not found:
                    players_unreachable.append(person)

            # build the reminder objects
            reminders = build_reminders()

            user_client = slack.WebClient(token=os.environ["USER_SLACK_TOKEN"])
            for reminder in reminders:
                # check if we don't need to ping this person
                if reminder.name not in PING_EXCLUDED_LIST:
                    # check if we already have a global reminder for this person
                    if REMINDERS.get(reminder.name, None) is None:
                        print(f"Adding reminder for {reminder.name}.")
                        reminder_id = user_client.reminders_add(
                            text=reminder.text, time=reminder.recurrence, user=reminder.id
                        )
                        REMINDERS[reminder.name] = reminder_id
                        players_being_reminded.append(reminder)

                    else:
                        players_already_reminded.append(reminder.name)
                        print(f"Already reminded {reminder.name}!!")

            # build message with what the result of the operation was
            for person in players_being_reminded:
                debt = person.text.split("\n")[0]
                text += f"Reminded {person.name}: {debt}\n"
            text += f"\n"
            for person in players_already_reminded:
                text += f"Skipped reminding {person}\n"
            text += f"\n"
            for person in players_unreachable:
                text += f"Couldn't find this person {person.name} but they owe {person.current_balance}\n"
            text += f"\n"
        elif toggle == "off":
            # clear all global objects, no more pinging
            PING_EXCLUDED_LIST.clear()
            PING_TIMES_PER_PLAYER.clear()
            PING_LIST.clear()
            REMINDERS.clear()
    else:
        text = f"This command can only be run by the {TEAM} TREASURERS, " \
               f"please contact *{TREASURERS}* if you think you should be able to run it."

    for reminder in REMINDERS:
        # for container logs!
        print(reminder)
    # send message with the result of the ping operation
    send_message_to_slack(user_id, channel_name, channel_id, text)
    

    return Response(), 200


@app.route("/ping_add", methods=["POST"])
def ping_add_players():
    """
    Add players to the global reminder list
    :return: Response 200 required by SLACK API
    """

    # get data from request
    data = request.form

    # get user information
    user_id = data.get("user_id")
    channel_id = data.get("channel_id")
    person_to_add = data.get("text")
    user = client.users_info(user=user_id)
    user_real_name = user.get("user").get("real_name")
    channel_name = data.get("channel_name")

    # make sure only TREASURERS can run this command
    if user_real_name in TREASURERS:
        slack_people = client.users_list().get("members")
        for person in slack_people:
            if person.get("real_name") == person_to_add:
                skipped = [person for person in PING_EXCLUDED_LIST if person != person_to_add]
                PING_EXCLUDED_LIST.clear()
                PING_EXCLUDED_LIST.extend(skipped)
        text = f"Adding {person_to_add} to the remind list.\n"
    else:
        text = f"This command can only be run by the {TEAM} TREASURERS, " \
               f"please contact *{TREASURERS}* if you think you should be able to run it."
    # send message with the result of the operation to TREASURERS
    send_message_to_slack(user_id, channel_name, channel_id, text)
    return Response(), 200


@app.route("/ping_remove", methods=["POST"])
def ping_remove_players():
    """
    Remove players for REMINDER list
    :return: Response 200 required by SLACK API
    """
    data = request.form
    user_id = data.get("user_id")
    channel_id = data.get("channel_id")
    print(data)
    print(user_id)
    print(data.get("text"))
    person_to_skip = data.get("text")
    user = client.users_info(user=user_id)
    user_real_name = user.get("user").get("real_name")
    channel_name = data.get("channel_name")

    # make sure only TREASURERS can run this command
    if user_real_name == TREASURERS:
        slack_people = client.users_list().get("members")
        for person in slack_people:
            if person.get("real_name") == person_to_skip:
                PING_EXCLUDED_LIST.append(person_to_skip)
        text = f"Skipping {person_to_skip} from future reminders \n"
    else:
        text = f"This command can only be run by the {TEAM} TREASURERS, " \
               f"please contact *{TREASURERS}* if you think you should be able to run it."
    send_message_to_slack(user_id, channel_name, channel_id, text)

    return Response(), 200


@app.route("/ping_adjust_time", methods=["POST"])
def ping_adjust_time():
    """
    Ping people at different times if they complained previously
    :return: Response 200 required by SLACK API
    """
    data = request.form
    user_id = data.get("user_id")
    channel_id = data.get("channel_id")
    info = data.get("text")
    user = client.users_info(user=user_id)
    user_real_name = user.get("user").get("real_name")
    channel_name = data.get("channel_name")

    # make sure only TREASURERS can run this command
    if user_real_name == TREASURERS:
        name, time = info.split(",")
        slack_people = [person.get("real_name") for person in client.users_list().get("members")]
        if name in slack_people:
            PING_TIMES_PER_PLAYER[name] = time
        text = f"adjusted reminders to *{PING_TIMES_PER_PLAYER[name]}* for _{name}_."
    else:
        text = f"This command can only be run by the {TEAM} TREASURERS, " \
               f"please contact *{TREASURERS}* if you think you should be able to run it."
    send_message_to_slack(user_id, channel_name, channel_id, text)

    return Response(), 200


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
