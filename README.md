# IMAMU Course Scheduler

A web application for Imam Mohammad Ibn Saud Islamic University (IMAMU) that uses Genetic Algorithm (GA) and Particle Swarm Optimization (PSO) to generate optimal course schedules.

## Features

### Student Dashboard
- Browse and filter courses by department/level
- Register for course sections
- Generate optimized schedules using GA or PSO
- Choose optimization objective (Student/Instructor/University)
- View top 3 schedule options in calendar view
- Save preferred schedules

### Instructor Dashboard
- Full course management (Create, Edit, Archive, Delete)
- Add sections to courses
- Register for courses
- Generate optimized schedules

### Schedule Optimization
- **Genetic Algorithm (GA)**: Three objectives - University, Instructor, Student
- **Particle Swarm Optimization (PSO)**: Student-focused optimization
- Hard constraints: room capacity, gender separation, prayer times, instructor conflicts
- Soft constraints: gap minimization, workload balance, late slot avoidance

## Tech Stack

- **Backend**: FastAPI (Python)
- **Frontend**: React
- **Database**: SQLite (default) / PostgreSQL (configurable)
- **Algorithms**: GA and PSO (custom implementations)

## Setup

### Prerequisites
- Python 3.10+
- Node.js 18+

### Installation

1. Clone the repository:
```bash
git clone https://github.com/anirudhatalmale6-alt/imamu-course-scheduler.git
cd imamu-course-scheduler
```

2. Install backend dependencies:
```bash
cd backend
pip install -r requirements.txt
```

3. Place your `university.xml` in the `backend/` directory

4. Install frontend dependencies and build:
```bash
cd ../frontend
npm install
npm run build
cp -r build ../backend/static
```

5. Start the server:
```bash
cd ../backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

6. Open http://localhost:8000 in your browser

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///course_scheduler.db` | Database connection URL |
| `SECRET_KEY` | (built-in) | JWT secret key |
| `UNIVERSITY_XML` | `university.xml` | Path to university data XML |

### Using PostgreSQL

Set the `DATABASE_URL` environment variable:
```bash
export DATABASE_URL=postgresql://user:pass@localhost:5432/course_scheduler
```

## Data Import

The application automatically imports data from `university.xml` on first startup. The XML contains:
- Faculty (40 professors, male/female)
- Campus rooms (60 rooms across 2 buildings)
- Academic program (51 courses, 270 sections)
- Time slots (9 class periods + 2 prayer breaks)
- Working days (Sunday-Thursday)

## API Endpoints

### Auth
- `POST /api/auth/register` - Register new user
- `POST /api/auth/login` - Login
- `GET /api/auth/me` - Get current user

### Courses
- `GET /api/courses` - List courses
- `POST /api/courses` - Create course (instructor only)
- `PUT /api/courses/{id}` - Update course
- `DELETE /api/courses/{id}` - Delete course

### Registration
- `POST /api/registration/register/{section_id}` - Register for section
- `DELETE /api/registration/unregister/{section_id}` - Drop section
- `GET /api/registration/my-registrations` - List registrations

### Schedule
- `POST /api/schedule/generate` - Generate optimized schedules
- `POST /api/schedule/save` - Save a schedule
- `GET /api/schedule/saved` - Get saved schedules
