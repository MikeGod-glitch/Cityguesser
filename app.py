from flask import Flask, render_template, request, session, redirect, url_for
import random

app = Flask(__name__)
app.secret_key = "city_game_secret"

cities = [
    {"image": "Chicago.png", "answer": "Chicago"},
    {"image": "London.png", "answer": "London"},
    {"image": "Tokyo.png", "answer": "Tokyo"}
]


def get_new_question():

    # 如果题库为空，重置
    if "remaining" not in session or len(session["remaining"]) == 0:
        session["remaining"] = list(range(len(cities)))

    remaining = session["remaining"]

    idx = random.choice(remaining)

    remaining.remove(idx)

    session["remaining"] = remaining
    session["current"] = idx

    return cities[idx]


@app.route("/")
def home():

    if "current" not in session:

        city = get_new_question()

    else:

        city = cities[session["current"]]

    return render_template(
        "index.html",
        image=city["image"],
        result=None
    )


@app.route("/check", methods=["POST"])
def check():

    guess = request.form["guess"]

    city = cities[session["current"]]

    if guess.strip().lower() == city["answer"].lower():

        result = "✅ Correct!"

    else:

        result = f"❌ Wrong! Answer: {city['answer']}"

    return render_template(
        "index.html",
        image=city["image"],
        result=result
    )


@app.route("/next")
def next_question():

    city = get_new_question()

    return render_template(
        "index.html",
        image=city["image"],
        result=None
    )


if __name__ == "__main__":
    app.run(debug=True)