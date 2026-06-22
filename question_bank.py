"""Curated bank of commonly-asked technical interview questions.

This is used as a lightweight retrieval ("RAG") layer: when generating interview
questions for a topic, we first pull the closest-matching real questions from this
bank and give them to the LLM as grounding reference, so generated questions reflect
realistic interview frequency instead of being purely invented by the model.
"""
from typing import Dict, List

QUESTION_BANK: List[Dict[str, str]] = [
    # Python
    {"skill_area": "Python", "difficulty": "easy", "text": "What is the difference between a list and a tuple in Python?"},
    {"skill_area": "Python", "difficulty": "easy", "text": "Explain Python's GIL (Global Interpreter Lock) and its impact on multithreading."},
    {"skill_area": "Python", "difficulty": "medium", "text": "What are Python decorators and how would you write one?"},
    {"skill_area": "Python", "difficulty": "medium", "text": "Explain the difference between deep copy and shallow copy in Python."},
    {"skill_area": "Python", "difficulty": "hard", "text": "How does Python's garbage collection work, and what is reference counting?"},
    {"skill_area": "Python", "difficulty": "medium", "text": "What is the difference between '==' and 'is' in Python?"},

    # JavaScript
    {"skill_area": "JavaScript", "difficulty": "easy", "text": "What is the difference between 'var', 'let', and 'const' in JavaScript?"},
    {"skill_area": "JavaScript", "difficulty": "medium", "text": "Explain closures in JavaScript with an example."},
    {"skill_area": "JavaScript", "difficulty": "medium", "text": "What is the event loop in JavaScript and how does it handle asynchronous code?"},
    {"skill_area": "JavaScript", "difficulty": "easy", "text": "What is the difference between '==' and '===' in JavaScript?"},
    {"skill_area": "JavaScript", "difficulty": "hard", "text": "Explain prototypal inheritance in JavaScript."},
    {"skill_area": "JavaScript", "difficulty": "medium", "text": "What are Promises and how do they differ from async/await?"},

    # React
    {"skill_area": "React", "difficulty": "easy", "text": "What is the virtual DOM and why does React use it?"},
    {"skill_area": "React", "difficulty": "medium", "text": "Explain the difference between useState and useRef in React."},
    {"skill_area": "React", "difficulty": "medium", "text": "What is the purpose of the useEffect hook and its dependency array?"},
    {"skill_area": "React", "difficulty": "medium", "text": "How does React's reconciliation algorithm decide what to re-render?"},
    {"skill_area": "React", "difficulty": "hard", "text": "What are React Server Components and how do they differ from traditional components?"},
    {"skill_area": "React", "difficulty": "easy", "text": "What is prop drilling and how can it be avoided?"},

    # SQL / Databases
    {"skill_area": "SQL", "difficulty": "easy", "text": "What is the difference between INNER JOIN and LEFT JOIN?"},
    {"skill_area": "SQL", "difficulty": "medium", "text": "What is database normalization and why is it important?"},
    {"skill_area": "SQL", "difficulty": "medium", "text": "Explain the difference between a primary key and a foreign key."},
    {"skill_area": "SQL", "difficulty": "hard", "text": "What are database indexes and how do they affect query performance?"},
    {"skill_area": "SQL", "difficulty": "medium", "text": "What is the difference between SQL and NoSQL databases, and when would you choose one over the other?"},

    # Data Structures & Algorithms
    {"skill_area": "Data Structures", "difficulty": "easy", "text": "What is the difference between an array and a linked list?"},
    {"skill_area": "Data Structures", "difficulty": "medium", "text": "Explain how a hash map works and how collisions are handled."},
    {"skill_area": "Data Structures", "difficulty": "medium", "text": "What is the difference between a stack and a queue, and where would you use each?"},
    {"skill_area": "Data Structures", "difficulty": "hard", "text": "Explain the time complexity of common sorting algorithms like quicksort and mergesort."},
    {"skill_area": "Data Structures", "difficulty": "medium", "text": "What is a binary search tree and how does search complexity compare to a regular array?"},

    # System Design
    {"skill_area": "System Design", "difficulty": "hard", "text": "How would you design a URL shortening service like bit.ly?"},
    {"skill_area": "System Design", "difficulty": "hard", "text": "What is the difference between horizontal and vertical scaling?"},
    {"skill_area": "System Design", "difficulty": "medium", "text": "What is a load balancer and why is it used in distributed systems?"},
    {"skill_area": "System Design", "difficulty": "hard", "text": "Explain the CAP theorem and its implications for distributed databases."},
    {"skill_area": "System Design", "difficulty": "medium", "text": "What is caching and what are common caching strategies?"},

    # OOP
    {"skill_area": "OOP", "difficulty": "easy", "text": "What are the four pillars of object-oriented programming?"},
    {"skill_area": "OOP", "difficulty": "medium", "text": "What is the difference between method overloading and method overriding?"},
    {"skill_area": "OOP", "difficulty": "medium", "text": "Explain the difference between an abstract class and an interface."},
    {"skill_area": "OOP", "difficulty": "hard", "text": "What is the SOLID principle and why does it matter in software design?"},

    # Web Development / Networking
    {"skill_area": "Web Development", "difficulty": "easy", "text": "What is the difference between HTTP and HTTPS?"},
    {"skill_area": "Web Development", "difficulty": "medium", "text": "What is CORS and why does it exist?"},
    {"skill_area": "Web Development", "difficulty": "medium", "text": "Explain the difference between cookies, local storage, and session storage."},
    {"skill_area": "Web Development", "difficulty": "medium", "text": "What is RESTful API design and what makes an API RESTful?"},
    {"skill_area": "Web Development", "difficulty": "hard", "text": "What is the difference between authentication and authorization?"},

    # DevOps / Git
    {"skill_area": "DevOps", "difficulty": "easy", "text": "What is the difference between 'git merge' and 'git rebase'?"},
    {"skill_area": "DevOps", "difficulty": "medium", "text": "What is CI/CD and why is it important in modern software development?"},
    {"skill_area": "DevOps", "difficulty": "medium", "text": "What is the difference between a Docker image and a Docker container?"},

    # Behavioral
    {"skill_area": "Behavioral", "difficulty": "easy", "text": "Tell me about a time you faced a difficult bug. How did you debug it?"},
    {"skill_area": "Behavioral", "difficulty": "easy", "text": "Describe a situation where you disagreed with a teammate. How did you resolve it?"},
    {"skill_area": "Behavioral", "difficulty": "medium", "text": "Tell me about a project you're most proud of and why."},
    {"skill_area": "Behavioral", "difficulty": "medium", "text": "How do you prioritize tasks when working on multiple deadlines?"},
]