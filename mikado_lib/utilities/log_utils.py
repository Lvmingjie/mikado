"""
Module which contains all functions related to logging.
"""


import logging

__author__ = 'Luca Venturini'


def create_null_logger(instance):
    """Static method to create a default logging instance for the loci.
    The default is a null handler (no log)

    :param instance: the instance used to derive a name for the logger. It must be either a string
    or a class instance with a __name__ attribute.
    """

    formatter = logging.Formatter(
        "{asctime} - {levelname}:{lineno} - {funcName} - {processName} - {message}",
        style="{"
        )

    if isinstance(instance, str):
        name = instance
    else:
        name = instance.__name__
    logger = create_default_logger(name)
    logger.removeHandler(logger.handlers[0])
    handler = logging.NullHandler()
    handler.setFormatter(formatter)
    logger.setLevel(logging.WARN)
    logger.addHandler(handler)
    return logger


def check_logger(logger):
    """Quick function to verify that a logger is really a logger,
    otherwise it raises a ValueError.

    :param logger: the logger instance
    :type logger: logging.Logger
    """

    if isinstance(logger, logging.Logger):
        return logger
    else:
        raise ValueError("{0} is not a logger but rather {1}".format(
            logger, type(logger)
        ))


def create_default_logger(name):
    """Default logger
    :param name: string used to give a name to the logger.
    :type name: str
    """

    formatter = logging.Formatter(
        "{asctime} - {levelname}:{lineno} - {funcName} - {processName} - {message}",
        style="{"
        )

    logger = logging.getLogger(name)
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger.setLevel(logging.WARN)
    logger.addHandler(handler)
    return logger
