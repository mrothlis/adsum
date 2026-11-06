# Examples

## `d2l-classlist-example.csv`

The shape of a D2L **Classlist export**, which Adsum imports to build a course
roster. Export your own from D2L with **Classlist → Export → Export all users**.

The columns that matter:

| Column | Used for |
| --- | --- |
| `Username` | The match key. It must be the D2L username, because that is what an LTI launch sends and what the gradebook import matches on. |
| `First Name`, `Last Name` | Display name on the roster and live attendance list. |
| `OrgDefinedId` | Institutional student ID, shown on the roster. |
| `Email` | Fallback for deriving a username if `Username` is blank. |

Extra columns are ignored, so an unmodified D2L export works as-is.

> **D2L truncates usernames and emails to 20 characters.** A long username
> arrives shortened, and that shortened form is the real identity for both LTI
> launches and gradebook imports. Do not "correct" it by hand.
