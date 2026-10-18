
import os
import mysql.connector
from mysql.connector import Error


def get_connection():
    return mysql.connector.connect(
        host="localhost",
        user="root",
        password="root",
        database="siteaudit_db"
    )


if __name__ == "__main__":
    try:
        connection = get_connection()

        if connection.is_connected():
            print("Connected to MySQL successfully!")

            cursor = connection.cursor()
            cursor.execute("SHOW TABLES")

            print("\nTables in siteaudit_db:")
            for table in cursor.fetchall():
                print("-", table[0])

            cursor.close()
            connection.close()

    except Error as error:
        print("MySQL connection failed:", error)
