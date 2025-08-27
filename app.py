from flask import Flask

app = Flask(__name__)

@app.route('/')
def welcome_message():
    return 'Server is LIVE'

if __name__ == '__main__':
    app.run(debug=True)
