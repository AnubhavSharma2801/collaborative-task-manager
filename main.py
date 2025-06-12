from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import google.oauth2.id_token
from google.auth.transport import requests
from google.cloud import firestore
from datetime import datetime

#define the app that will contain all of our routing for Fast API
app = FastAPI()

#we need a request object to be able to talk to firebase for verifying user logins
firebase_request_adapter = requests.Request()

#define the static and templates directories
app.mount('/static', StaticFiles(directory='static'), name='static')
templates = Jinja2Templates(directory="templates")

db = firestore.Client()

@app.get("/", response_class=HTMLResponse)
async def root(request: Request):
    # query firebase for the request token. we also declare a bunch of other variables here as we will need them
    # for rendering the template at the end. we have an error_message there in case you want to output an error to
    # the user in the template
    id_token = request.cookies.get("token")
    error_message = "No error here"
    user_token = None

    # if we have an ID token we will verify it against firebase. If it doesn't check out then log the error message that is returned
    if id_token:
        try:
            user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
        except ValueError as err:
            # dump this message to console as it will not be displayed on the template. use for debugging but if you are building for
            # production you should handle this much more gracefully.
            print(str(err))
    
    return templates.TemplateResponse('main.html', {"request":request, 'user_token':user_token, "error_message":error_message})

@app.get("/board/{board_id}", response_class=HTMLResponse)
async def view_board(request: Request, board_id: str):
    id_token = request.cookies.get("token")
    if not id_token:
        print("No token found")
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})
    
    try:
        user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
        if not user_token:
            print("Invalid token")
            return JSONResponse(status_code=401, content={"message": "Invalid token"})
        
        user_id = user_token["sub"]
        print(f"User ID: {user_id}")
        print(f"Board ID: {board_id}")
        
        # First check if the board exists in the user's collection
        user_board_ref = db.collection("users").document(user_id).collection("taskboards").document(board_id)
        user_board_doc = user_board_ref.get()
        
        if user_board_doc.exists:
            print("Found board in user's collection")
            board_data = user_board_doc.to_dict()
            print(f"Board members: {board_data.get('members', [])}")
        else:
            print("Board not found in user's collection, searching other users")
            # If not in user's collection, search all users
            board_found = False
            all_users = db.collection("users").stream()
            
            for user in all_users:
                print(f"Checking user: {user.id}")
                other_board_ref = db.collection("users").document(user.id).collection("taskboards").document(board_id)
                other_board_doc = other_board_ref.get()
                
                if other_board_doc.exists:
                    print(f"Found board in user {user.id}'s collection")
                    board_data = other_board_doc.to_dict()
                    print(f"Board members: {board_data.get('members', [])}")
                    # Check if the current user is a member
                    if user_id in board_data.get("members", []):
                        print("Current user is a member, copying board")
                        board_found = True
                        # Copy the board to user's collection if they don't have it
                        user_board_ref.set(board_data)
                        # Copy all tasks
                        tasks_ref = other_board_ref.collection("tasks").stream()
                        for task in tasks_ref:
                            task_data = task.to_dict()
                            user_board_ref.collection("tasks").document(task.id).set(task_data)
                        break
            
            if not board_found:
                print("Board not found in any user's collection")
                return JSONResponse(status_code=404, content={"message": "Board not found"})
        
        # At this point, we should have board_data
        if user_id not in board_data.get("members", []):
            print(f"Access denied. User {user_id} not in members list: {board_data.get('members', [])}")
            return JSONResponse(status_code=403, content={"message": "Access denied"})
        
        is_creator = user_id == board_data.get("creator_id")
        print(f"User is creator: {is_creator}")
        
        return templates.TemplateResponse('board.html', {
            "request": request,
            'user_token': user_token,
            "error_message": "No error here",
            "board_id": board_id,
            "is_creator": is_creator,
            "board_title": board_data.get("title", "")
        })
        
    except ValueError as e:
        print(f"Error in view_board: {str(e)}")
        return JSONResponse(status_code=401, content={"message": "Invalid token"})
    except Exception as e:
        print(f"Unexpected error in view_board: {str(e)}")
        return JSONResponse(status_code=500, content={"message": "Internal server error"})

@app.post("/boards")
async def create_board(request: Request):
    id_token = request.cookies.get("token")
    if not id_token:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})
    
    user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    if not user_token:
        return JSONResponse(status_code=401, content={"message": "Invalid token"})
    
    user_id = user_token["sub"]
    data = await request.json()
    
    # Create board under user's document
    board_ref = db.collection("users").document(user_id).collection("taskboards").document()
    board_data = {
        "title": data.get("title"),
        "creator_id": user_id,
        "members": [user_id],  # Initialize with creator
        "created_at": datetime.utcnow()
    }
    board_ref.set(board_data)
    
    return JSONResponse(status_code=201, content={"message": "Board created successfully", "board_id": board_ref.id})

@app.get("/boards")
async def get_boards(request: Request):
    id_token = request.cookies.get("token")
    if not id_token:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})
    
    user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    if not user_token:
        return JSONResponse(status_code=401, content={"message": "Invalid token"})
    
    user_id = user_token["sub"]
    
    # Get boards from user's collection
    boards_ref = db.collection("users").document(user_id).collection("taskboards").stream()
    
    # Convert Firestore documents to dict and handle datetime serialization
    boards = []
    for board in boards_ref:
        board_dict = board.to_dict()
        # Convert datetime to ISO format string
        if "created_at" in board_dict:
            board_dict["created_at"] = board_dict["created_at"].isoformat()
        boards.append({"id": board.id, **board_dict})
    
    return JSONResponse(status_code=200, content=boards)

@app.get("/boards/{board_id}/members")
async def get_board_members(board_id: str, request: Request):
    id_token = request.cookies.get("token")
    if not id_token:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})
    
    user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    if not user_token:
        return JSONResponse(status_code=401, content={"message": "Invalid token"})
    
    user_id = user_token["sub"]
    
    # Get board details from user's collection
    board_ref = db.collection("users").document(user_id).collection("taskboards").document(board_id)
    board_doc = board_ref.get()
    
    if not board_doc.exists:
        # Try to find the board in other users' collections
        all_users = db.collection("users").stream()
        board_found = False
        for user in all_users:
            board_ref = db.collection("users").document(user.id).collection("taskboards").document(board_id)
            board_doc = board_ref.get()
            if board_doc.exists:
                board_data = board_doc.to_dict()
                if user_id in board_data.get("members", []):
                    board_found = True
                    break
        
        if not board_found:
            return JSONResponse(status_code=404, content={"message": "Board not found"})
    
    board_data = board_doc.to_dict()
    
    # Check if user has access to the board
    if user_id not in board_data.get("members", []):
        return JSONResponse(status_code=403, content={"message": "Access denied"})
    
    # Get member details
    members = []
    for member_id in board_data.get("members", []):
        user_doc = db.collection("users").document(member_id).get()
        if user_doc.exists:
            user_data = user_doc.to_dict()
            members.append({
                "id": member_id,
                "email": user_data.get("email"),
                "is_creator": member_id == board_data.get("creator_id")
            })
    
    return JSONResponse(status_code=200, content=members)

@app.post("/boards/{board_id}/members")
async def add_board_member(board_id: str, request: Request):
    id_token = request.cookies.get("token")
    if not id_token:
        print("No token found in add_board_member")
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})
    
    try:
        user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
        if not user_token:
            print("Invalid token in add_board_member")
            return JSONResponse(status_code=401, content={"message": "Invalid token"})
        
        user_id = user_token["sub"]
        data = await request.json()
        member_email = data.get("email")
        
        print(f"Adding member with email: {member_email} to board: {board_id}")
        print(f"Creator ID: {user_id}")
        
        if not member_email:
            print("No email provided")
            return JSONResponse(status_code=400, content={"message": "Email is required"})
        
        # Get board details from creator's collection
        board_ref = db.collection("users").document(user_id).collection("taskboards").document(board_id)
        board_doc = board_ref.get()
        
        if not board_doc.exists:
            print("Board not found")
            return JSONResponse(status_code=404, content={"message": "Board not found"})
        
        board_data = board_doc.to_dict()
        print(f"Current board members: {board_data.get('members', [])}")
        
        # Check if user is the creator
        if user_id != board_data.get("creator_id"):
            print(f"User {user_id} is not the creator {board_data.get('creator_id')}")
            return JSONResponse(status_code=403, content={"message": "Only board creator can add members"})
        
        # Get user ID from email
        users_ref = db.collection("users").where("email", "==", member_email).limit(1).stream()
        user_docs = list(users_ref)
        
        if not user_docs:
            print(f"No user found with email: {member_email}")
            return JSONResponse(status_code=404, content={"message": "User not found. Make sure the user has signed up first."})
        
        member_id = user_docs[0].id
        print(f"Found member ID: {member_id}")
        
        # Check if member is already in the board
        if member_id in board_data.get("members", []):
            print("Member already in board")
            return JSONResponse(status_code=400, content={"message": "User is already a member of this board"})
        
        # Add member to board's members list
        if "members" not in board_data:
            board_data["members"] = [board_data["creator_id"]]
        board_data["members"].append(member_id)
        print(f"Updated members list: {board_data['members']}")
        
        # First update the board in the creator's collection
        board_ref.set(board_data)
        
        # Then create/update the board in the new member's collection
        member_board_ref = db.collection("users").document(member_id).collection("taskboards").document(board_id)
        member_board_ref.set(board_data)
        
        # Copy all tasks to the new member's board
        tasks_ref = board_ref.collection("tasks").stream()
        for task in tasks_ref:
            task_data = task.to_dict()
            member_board_ref.collection("tasks").document(task.id).set(task_data)
        
        # Update the board in all other members' collections to keep members list in sync
        for existing_member_id in board_data["members"]:
            if existing_member_id not in [user_id, member_id]:  # Skip creator and new member
                existing_member_ref = db.collection("users").document(existing_member_id).collection("taskboards").document(board_id)
                existing_member_ref.set(board_data)
        
        print(f"Board updated in all collections with members: {board_data['members']}")
        return JSONResponse(status_code=200, content={"message": "Member added successfully"})
        
    except Exception as e:
        print(f"Error in add_board_member: {str(e)}")
        return JSONResponse(status_code=500, content={"message": f"Failed to add member: {str(e)}"})

@app.post("/boards/{board_id}/tasks")
async def create_task(board_id: str, request: Request):
    id_token = request.cookies.get("token")
    if not id_token:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})
    
    user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    if not user_token:
        return JSONResponse(status_code=401, content={"message": "Invalid token"})
    
    user_id = user_token["sub"]
    data = await request.json()
    
    # Get board details from user's collection
    board_ref = db.collection("users").document(user_id).collection("taskboards").document(board_id)
    board_doc = board_ref.get()
    
    if not board_doc.exists:
        return JSONResponse(status_code=404, content={"message": "Board not found"})
    
    board_data = board_doc.to_dict()
    
    # Check if user has access to the board
    if user_id not in board_data.get("members", []):
        return JSONResponse(status_code=403, content={"message": "Access denied"})
    
    # Check for duplicate task names
    tasks_ref = board_ref.collection("tasks").where("title", "==", data.get("title")).stream()
    if any(task.exists for task in tasks_ref):
        return JSONResponse(status_code=400, content={"message": "A task with this name already exists"})
    
    # Create task data
    task_data = {
        "title": data.get("title"),
        "description": data.get("description", ""),
        "due_date": datetime.fromisoformat(data.get("due_date").replace('Z', '+00:00')),
        "completed": False,
        "completed_at": None,
        "created_at": datetime.utcnow(),
        "assigned_to": data.get("assigned_to", None),
        "created_by": user_id
    }
    
    # Create task with the same ID in all member collections
    task_id = board_ref.collection("tasks").document().id  # Generate a new task ID
    
    # Add task to all member collections
    for member_id in board_data.get("members", []):
        member_board_ref = db.collection("users").document(member_id).collection("taskboards").document(board_id)
        member_board_ref.collection("tasks").document(task_id).set(task_data)
    
    return JSONResponse(status_code=201, content={"message": "Task created successfully"})

@app.get("/boards/{board_id}/tasks")
async def get_board_tasks(board_id: str, request: Request):
    id_token = request.cookies.get("token")
    if not id_token:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})
    
    user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    if not user_token:
        return JSONResponse(status_code=401, content={"message": "Invalid token"})
    
    user_id = user_token["sub"]
    
    # Get board details from user's collection
    board_ref = db.collection("users").document(user_id).collection("taskboards").document(board_id)
    board_doc = board_ref.get()
    
    if not board_doc.exists:
        return JSONResponse(status_code=404, content={"message": "Board not found"})
    
    board_data = board_doc.to_dict()
    
    # Check if user has access to the board
    if user_id not in board_data.get("members", []):
        return JSONResponse(status_code=403, content={"message": "Access denied"})
    
    # Get tasks from board's collection
    tasks_ref = board_ref.collection("tasks").stream()
    
    # Convert Firestore documents to dict and handle datetime serialization
    tasks = []
    for task in tasks_ref:
        task_dict = task.to_dict()
        # Convert datetime fields to ISO format strings
        if "due_date" in task_dict:
            task_dict["due_date"] = task_dict["due_date"].isoformat()
        if "completed_at" in task_dict and task_dict["completed_at"]:
            task_dict["completed_at"] = task_dict["completed_at"].isoformat()
        if "created_at" in task_dict:
            task_dict["created_at"] = task_dict["created_at"].isoformat()
        tasks.append({"id": task.id, **task_dict})
    
    return JSONResponse(status_code=200, content=tasks)

@app.put("/tasks/{task_id}")
async def update_task(task_id: str, request: Request):
    id_token = request.cookies.get("token")
    if not id_token:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})
    
    user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    if not user_token:
        return JSONResponse(status_code=401, content={"message": "Invalid token"})
    
    user_id = user_token["sub"]
    data = await request.json()
    
    # Get board ID from the request
    board_id = data.get("board_id")
    if not board_id:
        return JSONResponse(status_code=400, content={"message": "Board ID is required"})
    
    # Get board details from user's collection
    board_ref = db.collection("users").document(user_id).collection("taskboards").document(board_id)
    board_doc = board_ref.get()
    
    if not board_doc.exists:
        return JSONResponse(status_code=404, content={"message": "Board not found"})
    
    board_data = board_doc.to_dict()
    
    # Check if user has access to the board
    if user_id not in board_data.get("members", []):
        return JSONResponse(status_code=403, content={"message": "Access denied"})
    
    # Get task from board's collection
    task_ref = board_ref.collection("tasks").document(task_id)
    task_doc = task_ref.get()
    
    if not task_doc.exists:
        return JSONResponse(status_code=404, content={"message": "Task not found"})
    
    task_data = task_doc.to_dict()
    
    # Check for duplicate task names if title is being updated
    if "title" in data and data["title"] != task_data["title"]:
        tasks_ref = board_ref.collection("tasks").where("title", "==", data["title"]).stream()
        if any(task.exists for task in tasks_ref):
            return JSONResponse(status_code=400, content={"message": "A task with this name already exists"})
    
    # Update task data
    update_data = {}
    if "title" in data:
        update_data["title"] = data["title"]
    if "description" in data:
        update_data["description"] = data["description"]
    if "due_date" in data:
        update_data["due_date"] = datetime.fromisoformat(data["due_date"].replace('Z', '+00:00'))
    if "completed" in data:
        update_data["completed"] = data["completed"]
        if data["completed"]:
            update_data["completed_at"] = datetime.utcnow()
        else:
            update_data["completed_at"] = None
    if "assigned_to" in data:
        update_data["assigned_to"] = data["assigned_to"]
    
    # Update task in all member collections
    for member_id in board_data.get("members", []):
        try:
            member_board_ref = db.collection("users").document(member_id).collection("taskboards").document(board_id)
            member_task_ref = member_board_ref.collection("tasks").document(task_id)
            
            # Check if task exists in member's collection
            member_task_doc = member_task_ref.get()
            if not member_task_doc.exists:
                # If task doesn't exist, create it with full task data
                full_task_data = task_data.copy()
                full_task_data.update(update_data)
                member_task_ref.set(full_task_data)
            else:
                # If task exists, just update it
                member_task_ref.update(update_data)
        except Exception as e:
            print(f"Error updating task for member {member_id}: {str(e)}")
            continue
    
    return JSONResponse(status_code=200, content={"message": "Task updated successfully"})

@app.delete("/tasks/{task_id}")
async def delete_task(task_id: str, request: Request):
    id_token = request.cookies.get("token")
    if not id_token:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})
    
    user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    if not user_token:
        return JSONResponse(status_code=401, content={"message": "Invalid token"})
    
    user_id = user_token["sub"]
    
    # Get board ID from the request
    board_id = request.query_params.get("board_id")
    if not board_id:
        return JSONResponse(status_code=400, content={"message": "Board ID is required"})
    
    # Get board details from user's collection
    board_ref = db.collection("users").document(user_id).collection("taskboards").document(board_id)
    board_doc = board_ref.get()
    
    if not board_doc.exists:
        return JSONResponse(status_code=404, content={"message": "Board not found"})
    
    board_data = board_doc.to_dict()
    
    # Check if user has access to the board
    if user_id not in board_data.get("members", []):
        return JSONResponse(status_code=403, content={"message": "Access denied"})
    
    # Delete task from all member collections
    for member_id in board_data.get("members", []):
        member_board_ref = db.collection("users").document(member_id).collection("taskboards").document(board_id)
        member_task_ref = member_board_ref.collection("tasks").document(task_id)
        member_task_ref.delete()
    
    return JSONResponse(status_code=200, content={"message": "Task deleted successfully"})

@app.get("/boards/{board_id}/stats")
async def get_board_stats(board_id: str, request: Request):
    id_token = request.cookies.get("token")
    if not id_token:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})
    
    user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    if not user_token:
        return JSONResponse(status_code=401, content={"message": "Invalid token"})
    
    user_id = user_token["sub"]
    
    # Get board details from user's collection
    board_ref = db.collection("users").document(user_id).collection("taskboards").document(board_id)
    board_doc = board_ref.get()
    
    if not board_doc.exists:
        return JSONResponse(status_code=404, content={"message": "Board not found"})
    
    board_data = board_doc.to_dict()
    
    # Check if user has access to the board
    if user_id not in board_data.get("members", []):
        return JSONResponse(status_code=403, content={"message": "Access denied"})
    
    # Get tasks from board's collection
    tasks_ref = board_ref.collection("tasks").stream()
    tasks = [task.to_dict() for task in tasks_ref]
    
    stats = {
        "total_tasks": len(tasks),
        "active_tasks": len([t for t in tasks if not t.get("completed", False)]),
        "completed_tasks": len([t for t in tasks if t.get("completed", False)]),
        "unassigned_tasks": len([t for t in tasks if not t.get("assigned_to")])
    }
    
    return JSONResponse(status_code=200, content=stats)

@app.put("/boards/{board_id}")
async def update_board(board_id: str, request: Request):
    id_token = request.cookies.get("token")
    if not id_token:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})
    
    user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    if not user_token:
        return JSONResponse(status_code=401, content={"message": "Invalid token"})
    
    user_id = user_token["sub"]
    data = await request.json()
    
    # Get board details
    board_ref = db.collection("taskboards").document(board_id)
    board_doc = board_ref.get()
    
    if not board_doc.exists:
        return JSONResponse(status_code=404, content={"message": "Board not found"})
    
    board_data = board_doc.to_dict()
    
    # Check if user is the creator
    if user_id != board_data.get("creator_id"):
        return JSONResponse(status_code=403, content={"message": "Only board creator can update the board"})
    
    # Update board
    update_data = {}
    if "title" in data:
        update_data["title"] = data["title"]
    
    board_ref.update(update_data)
    
    return JSONResponse(status_code=200, content={"message": "Board updated successfully"})

@app.delete("/boards/{board_id}")
async def delete_board(board_id: str, request: Request):
    id_token = request.cookies.get("token")
    if not id_token:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})
    
    user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    if not user_token:
        return JSONResponse(status_code=401, content={"message": "Invalid token"})
    
    user_id = user_token["sub"]
    
    # Get board details
    board_ref = db.collection("taskboards").document(board_id)
    board_doc = board_ref.get()
    
    if not board_doc.exists:
        return JSONResponse(status_code=404, content={"message": "Board not found"})
    
    board_data = board_doc.to_dict()
    
    # Check if user is the creator
    if user_id != board_data.get("creator_id"):
        return JSONResponse(status_code=403, content={"message": "Only board creator can delete the board"})
    
    # Check if there are any tasks
    tasks_ref = db.collection("tasks").where("board_id", "==", board_id).stream()
    if any(task.exists for task in tasks_ref):
        return JSONResponse(status_code=400, content={"message": "Cannot delete board with existing tasks"})
    
    # Check if there are any non-owning members
    if len(board_data.get("members", [])) > 1:
        return JSONResponse(status_code=400, content={"message": "Cannot delete board with existing members"})
    
    # Delete board
    board_ref.delete()
    
    return JSONResponse(status_code=200, content={"message": "Board deleted successfully"})

@app.delete("/boards/{board_id}/members/{member_id}")
async def remove_board_member(board_id: str, member_id: str, request: Request):
    id_token = request.cookies.get("token")
    if not id_token:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})
    
    user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    if not user_token:
        return JSONResponse(status_code=401, content={"message": "Invalid token"})
    
    user_id = user_token["sub"]
    
    # Get board details from user's collection
    board_ref = db.collection("users").document(user_id).collection("taskboards").document(board_id)
    board_doc = board_ref.get()
    
    if not board_doc.exists:
        return JSONResponse(status_code=404, content={"message": "Board not found"})
    
    board_data = board_doc.to_dict()
    
    # Check if user is the creator
    if user_id != board_data.get("creator_id"):
        return JSONResponse(status_code=403, content={"message": "Only board creator can remove members"})
    
    # Check if member exists
    if member_id not in board_data.get("members", []):
        return JSONResponse(status_code=404, content={"message": "Member not found"})
    
    # Check if trying to remove the creator
    if member_id == board_data.get("creator_id"):
        return JSONResponse(status_code=400, content={"message": "Cannot remove the board creator"})
    
    # Remove member from board in creator's collection
    board_ref.update({
        "members": firestore.ArrayRemove([member_id])
    })
    
    # Mark member's tasks as unassigned in creator's collection
    tasks_ref = board_ref.collection("tasks").where("assigned_to", "==", member_id).stream()
    for task in tasks_ref:
        task.reference.update({
            "assigned_to": None,
            "previously_assigned": member_id  # Add this field to track previously assigned user
        })
    
    # Remove board from member's collection
    member_board_ref = db.collection("users").document(member_id).collection("taskboards").document(board_id)
    member_board_ref.delete()
    
    return JSONResponse(status_code=200, content={"message": "Member removed successfully"})

@app.post("/users")
async def create_user(request: Request):
    id_token = request.cookies.get("token")
    if not id_token:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})
    
    user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    if not user_token:
        return JSONResponse(status_code=401, content={"message": "Invalid token"})
    
    data = await request.json()
    user_id = user_token["sub"]
    
    # Check if user document already exists
    user_ref = db.collection("users").document(user_id)
    user_doc = user_ref.get()
    
    if user_doc.exists:
        return JSONResponse(status_code=200, content={"message": "User document already exists"})
    
    # Create user document
    user_ref.set({
        "email": user_token["email"],  # Use email from verified token
        "created_at": datetime.utcnow()
    })
    
    return JSONResponse(status_code=201, content={"message": "User created successfully"})

@app.post("/users/check")
async def check_user_exists(request: Request):
    data = await request.json()
    email = data.get("email")
    
    if not email:
        return JSONResponse(status_code=400, content={"message": "Email is required"})
    
    # Query Firestore for user with this email
    users_ref = db.collection("users").where("email", "==", email).limit(1).stream()
    user_exists = any(user.exists for user in users_ref)
    
    return JSONResponse(status_code=200, content={"exists": user_exists})

@app.put("/boards/{board_id}/rename")
async def rename_board(board_id: str, request: Request):
    id_token = request.cookies.get("token")
    if not id_token:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})
    
    user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    if not user_token:
        return JSONResponse(status_code=401, content={"message": "Invalid token"})
    
    user_id = user_token["sub"]
    data = await request.json()
    new_title = data.get("title")
    
    if not new_title:
        return JSONResponse(status_code=400, content={"message": "Title is required"})
    
    # Get board from user's collection
    board_ref = db.collection("users").document(user_id).collection("taskboards").document(board_id)
    board_doc = board_ref.get()
    
    if not board_doc.exists:
        return JSONResponse(status_code=404, content={"message": "Board not found"})
    
    board_data = board_doc.to_dict()
    
    # Check if user is the creator
    if user_id != board_data.get("creator_id"):
        return JSONResponse(status_code=403, content={"message": "Only board creator can rename the board"})
    
    # Update board title
    board_ref.update({"title": new_title})
    
    # Update title in all members' collections
    for member_id in board_data.get("members", []):
        if member_id != user_id:  # Skip creator as we already updated their copy
            member_board_ref = db.collection("users").document(member_id).collection("taskboards").document(board_id)
            member_board_ref.update({"title": new_title})
    
    return JSONResponse(status_code=200, content={"message": "Board renamed successfully"})

@app.get("/users")
async def get_all_users(request: Request):
    id_token = request.cookies.get("token")
    if not id_token:
        return JSONResponse(status_code=401, content={"message": "Unauthorized"})
    
    user_token = google.oauth2.id_token.verify_firebase_token(id_token, firebase_request_adapter)
    if not user_token:
        return JSONResponse(status_code=401, content={"message": "Invalid token"})
    
    # Get all users from Firestore
    users_ref = db.collection("users").stream()
    users = []
    for user in users_ref:
        user_data = user.to_dict()
        users.append({
            "id": user.id,
            "email": user_data.get("email")
        })
    
    return JSONResponse(status_code=200, content=users)