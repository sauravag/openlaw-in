"""Adversarial gate: the attacks this system must survive, run in CI.

Not a one-off exercise. Every case here asserts a STRUCTURAL outcome -- a
namespace boundary, a response field, a status code -- never anything about
model wording, because wording is the one thing an adversary and a model
upgrade can both change.
"""
