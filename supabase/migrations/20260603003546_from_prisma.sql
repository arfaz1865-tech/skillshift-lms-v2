-- CreateSchema
CREATE SCHEMA IF NOT EXISTS "public";

-- CreateEnum
CREATE TYPE "CourseStatus" AS ENUM ('DRAFT', 'PUBLISHED', 'ARCHIVED');

-- CreateEnum
CREATE TYPE "PricingType" AS ENUM ('FREE', 'PAID');

-- CreateEnum
CREATE TYPE "LessonType" AS ENUM ('VIDEO', 'QUIZ', 'ASSIGNMENT');

-- CreateEnum
CREATE TYPE "EnrollmentStatus" AS ENUM ('ACTIVE', 'COMPLETED', 'CANCELLED');

-- CreateEnum
CREATE TYPE "LessonProgressStatus" AS ENUM ('NOT_STARTED', 'IN_PROGRESS', 'COMPLETED');

-- CreateEnum
CREATE TYPE "QuestionType" AS ENUM ('SINGLE_CHOICE', 'MULTIPLE_CHOICE', 'TRUE_FALSE');

-- CreateEnum
CREATE TYPE "SubmissionStatus" AS ENUM ('SUBMITTED', 'GRADED');

-- CreateTable
CREATE TABLE "students" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "firstName" TEXT NOT NULL,
    "lastName" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "phoneNumber" TEXT,
    "dob" TIMESTAMP(3),
    "gender" TEXT,
    "country" TEXT,
    "city" TEXT,
    "bio" TEXT,
    "profileImage" TEXT,
    "futureGoal" TEXT,
    "accountStatus" TEXT NOT NULL DEFAULT 'active',
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "user_auth_id" UUID DEFAULT gen_random_uuid(),

    CONSTRAINT "students_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "instructors" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "firstName" TEXT NOT NULL,
    "lastName" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "phoneNumber" TEXT,
    "gender" TEXT,
    "dob" TIMESTAMPTZ(6),
    "bio" TEXT,
    "profileImage" TEXT,
    "city" TEXT,
    "country" TEXT,
    "accountType" TEXT,
    "accountStatus" TEXT NOT NULL DEFAULT 'active',
    "qualification" TEXT,
    "expertiseArea" TEXT,
    "yearOfExperience" INTEGER,
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "user_auth_id" UUID DEFAULT gen_random_uuid(),

    CONSTRAINT "instructors_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "admins" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "firstName" TEXT NOT NULL,
    "lastName" TEXT NOT NULL,
    "email" TEXT NOT NULL,
    "phoneNumber" TEXT,
    "profileImage" TEXT,
    "accountStatus" TEXT NOT NULL DEFAULT 'active',
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "user_auth_id" UUID DEFAULT gen_random_uuid(),

    CONSTRAINT "admins_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "courses" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "instructorId" UUID NOT NULL,
    "title" TEXT NOT NULL,
    "slug" TEXT NOT NULL,
    "shortDescription" TEXT,
    "description" TEXT,
    "thumbnail" TEXT,
    "trailerVideoUrl" TEXT,
    "category" TEXT,
    "subCategory" TEXT,
    "tags" TEXT[],
    "level" TEXT,
    "language" TEXT,
    "status" "CourseStatus" NOT NULL DEFAULT 'DRAFT',
    "pricingType" "PricingType" NOT NULL DEFAULT 'FREE',
    "price" DECIMAL(65,30) NOT NULL DEFAULT 0,
    "discountPrice" DECIMAL(65,30),
    "totalDurationMinutes" INTEGER NOT NULL DEFAULT 0,
    "totalLessons" INTEGER NOT NULL DEFAULT 0,
    "totalQuizzes" INTEGER NOT NULL DEFAULT 0,
    "totalAssignments" INTEGER NOT NULL DEFAULT 0,
    "requirements" TEXT[],
    "learningOutcomes" TEXT[],
    "targetAudience" TEXT[],
    "publishedAt" TIMESTAMPTZ(6),
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "courses_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "courseModules" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "courseId" UUID NOT NULL,
    "title" TEXT NOT NULL,
    "description" TEXT,
    "position" INTEGER NOT NULL,
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "courseModules_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "lessons" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "courseId" UUID NOT NULL,
    "moduleId" UUID NOT NULL,
    "title" TEXT NOT NULL,
    "content" TEXT,
    "lessonType" "LessonType" NOT NULL,
    "order" INTEGER NOT NULL,
    "isPreview" BOOLEAN NOT NULL DEFAULT false,
    "estimatedDurationMinutes" INTEGER NOT NULL DEFAULT 0,
    "videoUrl" TEXT,
    "videoDurationSeconds" INTEGER,
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "lessons_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "enrollments" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "courseId" UUID NOT NULL,
    "studentId" UUID NOT NULL,
    "completed" BOOLEAN NOT NULL DEFAULT false,
    "status" "EnrollmentStatus" NOT NULL DEFAULT 'ACTIVE',
    "progressPercentage" DECIMAL(65,30) NOT NULL DEFAULT 0,
    "completedLessons" INTEGER NOT NULL DEFAULT 0,
    "enrolledAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "completedAt" TIMESTAMPTZ(6),
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "enrollments_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "lessonProgress" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "enrollmentId" UUID NOT NULL,
    "studentId" UUID NOT NULL,
    "courseId" UUID NOT NULL,
    "lessonId" UUID NOT NULL,
    "status" "LessonProgressStatus" NOT NULL DEFAULT 'NOT_STARTED',
    "progressPercentage" DECIMAL(65,30) NOT NULL DEFAULT 0,
    "watchTimeSeconds" INTEGER NOT NULL DEFAULT 0,
    "lastVideoPositionSeconds" INTEGER NOT NULL DEFAULT 0,
    "completedAt" TIMESTAMPTZ(6),
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "lessonProgress_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "quizzes" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "lessonId" UUID NOT NULL,
    "title" TEXT NOT NULL,
    "description" TEXT,
    "passingPercentage" DECIMAL(65,30) NOT NULL DEFAULT 70,
    "durationMinutes" INTEGER,
    "attemptsAllowed" INTEGER NOT NULL DEFAULT 1,
    "showCorrectAnswers" BOOLEAN NOT NULL DEFAULT true,
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "quizzes_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "quizQuestions" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "quizId" UUID NOT NULL,
    "question" TEXT NOT NULL,
    "questionType" "QuestionType" NOT NULL,
    "marks" INTEGER NOT NULL DEFAULT 1,
    "position" INTEGER NOT NULL,

    CONSTRAINT "quizQuestions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "quizOptions" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "questionId" UUID NOT NULL,
    "optionText" TEXT NOT NULL,
    "isCorrect" BOOLEAN NOT NULL DEFAULT false,

    CONSTRAINT "quizOptions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "quizAttempts" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "quizId" UUID NOT NULL,
    "studentId" UUID NOT NULL,
    "score" DECIMAL(65,30),
    "percentage" DECIMAL(65,30),
    "passed" BOOLEAN NOT NULL DEFAULT false,
    "startedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "submittedAt" TIMESTAMPTZ(6),
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "quizAttempts_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "quizAttemptAnswers" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "attemptId" UUID NOT NULL,
    "questionId" UUID NOT NULL,
    "selectedOptionId" UUID,
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "quizAttemptAnswers_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "assignments" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "lessonId" UUID NOT NULL,
    "title" TEXT NOT NULL,
    "description" TEXT,
    "instructions" TEXT,
    "maxMarks" INTEGER NOT NULL DEFAULT 100,
    "dueDate" TIMESTAMPTZ(6),
    "allowLateSubmission" BOOLEAN NOT NULL DEFAULT true,
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "assignments_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "assignmentSubmissions" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "lessonId" UUID NOT NULL,
    "enrollmentId" UUID NOT NULL,
    "studentId" UUID NOT NULL,
    "gradedBy" UUID,
    "answerText" TEXT NOT NULL,
    "submissionText" TEXT,
    "attachmentUrl" TEXT,
    "submittedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "status" "SubmissionStatus" NOT NULL DEFAULT 'SUBMITTED',
    "marks" DECIMAL(65,30),
    "feedback" TEXT,
    "gradedAt" TIMESTAMPTZ(6),
    "assignmentId" UUID,
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "assignmentSubmissions_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "courseRatings" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "courseId" UUID NOT NULL,
    "studentId" UUID NOT NULL,
    "rating" INTEGER NOT NULL,
    "review" TEXT,
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "courseRatings_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "lessonComments" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "lessonId" UUID NOT NULL,
    "studentId" UUID NOT NULL,
    "comment" TEXT NOT NULL,
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "lessonComments_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "certificates" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "courseId" UUID NOT NULL,
    "studentId" UUID NOT NULL,
    "certificateNumber" TEXT NOT NULL,
    "certificateUrl" TEXT,
    "issuedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "certificates_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "companies" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "companyName" TEXT NOT NULL,
    "companyEmail" TEXT NOT NULL,
    "companyPassword" TEXT NOT NULL,
    "phoneNumber" TEXT,
    "companyLogo" TEXT,
    "companyWebsite" TEXT,
    "companyDescription" TEXT,
    "companyLocation" TEXT,
    "city" TEXT,
    "country" TEXT,
    "companyType" TEXT,
    "industryType" TEXT,
    "companySize" TEXT,
    "visionStatement" TEXT,
    "accountType" TEXT,
    "accountStatus" TEXT NOT NULL DEFAULT 'active',
    "regNumber" TEXT,
    "regDate" TIMESTAMPTZ(6),
    "invitationLimit" INTEGER DEFAULT 0,
    "pendingInvitation" INTEGER DEFAULT 0,
    "totalInvited" INTEGER NOT NULL DEFAULT 0,
    "activeStudent" INTEGER NOT NULL DEFAULT 0,
    "completedStudent" INTEGER NOT NULL DEFAULT 0,
    "activeCourse" INTEGER NOT NULL DEFAULT 0,
    "completedCourse" INTEGER NOT NULL DEFAULT 0,
    "totalCourseRequested" INTEGER NOT NULL DEFAULT 0,
    "subscriptionPlan" TEXT,
    "subscriptionStatus" TEXT,
    "subscriptionStartAt" TIMESTAMPTZ(6),
    "subscriptionEndAt" TIMESTAMPTZ(6),
    "lastLogin" TIMESTAMPTZ(6),
    "passwordLastChange" TIMESTAMPTZ(6),
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "companies_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "roadmaps" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "roadmapTitle" TEXT NOT NULL,
    "roadmapDescription" TEXT,
    "roadmapStatus" TEXT,
    "courseInProgress" INTEGER DEFAULT 0,
    "courseCompleted" INTEGER DEFAULT 0,
    "completionPercent" DOUBLE PRECISION DEFAULT 0,
    "totalCourse" INTEGER DEFAULT 0,
    "mandatoryCourse" INTEGER DEFAULT 0,
    "optionalCourse" INTEGER DEFAULT 0,
    "prequisiteCourse" INTEGER DEFAULT 0,
    "courseSequence" TEXT,
    "difficultyLevel" TEXT,
    "estimatedDuration" INTEGER,
    "totalSection" INTEGER DEFAULT 0,
    "totalLearningHour" INTEGER DEFAULT 0,
    "startAt" TIMESTAMPTZ(6),
    "expectedCompDate" TIMESTAMPTZ(6),
    "generationDate" TIMESTAMPTZ(6),
    "lastActivityDate" TIMESTAMPTZ(6),
    "lastAiAdjustmentDate" TIMESTAMPTZ(6),
    "deletedAt" TIMESTAMPTZ(6),
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "roadmaps_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "resumes" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "resumeTitle" TEXT NOT NULL,
    "resumeTemplate" TEXT,
    "resumeFilePath" TEXT,
    "fileUrl" TEXT,
    "resumeFormat" TEXT,
    "fileSize" INTEGER,
    "totalPages" INTEGER,
    "templateStyle" TEXT,
    "fontStyle" TEXT,
    "colorSchema" TEXT,
    "layoutStyle" TEXT,
    "generationStatus" TEXT,
    "generationDate" TIMESTAMPTZ(6),
    "resumeVersion" INTEGER DEFAULT 1,
    "resumeScore" DOUBLE PRECISION,
    "resumeStatus" TEXT NOT NULL DEFAULT 'active',
    "deletedAt" TIMESTAMPTZ(6),
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "resumes_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "payments" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "studentId" UUID NOT NULL,
    "courseId" UUID NOT NULL,
    "amount" DECIMAL(65,30) NOT NULL DEFAULT 0,
    "currency" TEXT NOT NULL DEFAULT 'usd',
    "paymentMethod" TEXT,
    "stripePaymentIntentId" TEXT,
    "stripeChargeId" TEXT,
    "status" TEXT NOT NULL DEFAULT 'pending',
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "completedAt" TIMESTAMPTZ(6),
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "payments_pkey" PRIMARY KEY ("id")
);

-- CreateTable
CREATE TABLE "invoices" (
    "id" UUID NOT NULL DEFAULT gen_random_uuid(),
    "invoiceType" TEXT,
    "invoiceStatus" TEXT,
    "invoiceMethod" TEXT,
    "invoiceGateway" TEXT,
    "transactionId" TEXT,
    "gatewayTransactionId" TEXT,
    "invoiceAmount" DOUBLE PRECISION NOT NULL,
    "taxAmount" DOUBLE PRECISION DEFAULT 0,
    "totalAmount" DOUBLE PRECISION NOT NULL,
    "discountApplied" DOUBLE PRECISION DEFAULT 0,
    "discountCode" TEXT,
    "platformCommission" DOUBLE PRECISION DEFAULT 0,
    "instructorShare" DOUBLE PRECISION DEFAULT 0,
    "currencyType" TEXT,
    "billingCycle" TEXT,
    "nextBillingDate" TIMESTAMPTZ(6),
    "cardType" TEXT,
    "cardLastFourDigit" TEXT,
    "deviceInfo" TEXT,
    "ipAddress" TEXT,
    "isSuccessful" BOOLEAN NOT NULL DEFAULT false,
    "failureReason" TEXT,
    "deletedAt" TIMESTAMPTZ(6),
    "receiptSent" BOOLEAN NOT NULL DEFAULT false,
    "receiptSentEmail" TEXT,
    "receiptSentDate" TIMESTAMPTZ(6),
    "receiptUrl" TEXT,
    "instructorPayoutStatus" TEXT,
    "instructorPayoutDate" TIMESTAMPTZ(6),
    "invoiceDate" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "invoiceCompletedAt" TIMESTAMPTZ(6),
    "createdAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,
    "updatedAt" TIMESTAMPTZ(6) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "invoices_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "students_email_key" ON "students"("email");

-- CreateIndex
CREATE UNIQUE INDEX "instructors_email_key" ON "instructors"("email");

-- CreateIndex
CREATE UNIQUE INDEX "admins_email_key" ON "admins"("email");

-- CreateIndex
CREATE UNIQUE INDEX "courses_slug_key" ON "courses"("slug");

-- CreateIndex
CREATE INDEX "courses_instructorId_idx" ON "courses"("instructorId");

-- CreateIndex
CREATE INDEX "courseModules_courseId_idx" ON "courseModules"("courseId");

-- CreateIndex
CREATE INDEX "lessons_courseId_idx" ON "lessons"("courseId");

-- CreateIndex
CREATE INDEX "lessons_moduleId_idx" ON "lessons"("moduleId");

-- CreateIndex
CREATE INDEX "lessons_courseId_moduleId_order_idx" ON "lessons"("courseId", "moduleId", "order");

-- CreateIndex
CREATE INDEX "enrollments_studentId_idx" ON "enrollments"("studentId");

-- CreateIndex
CREATE INDEX "enrollments_courseId_idx" ON "enrollments"("courseId");

-- CreateIndex
CREATE UNIQUE INDEX "enrollments_courseId_studentId_key" ON "enrollments"("courseId", "studentId");

-- CreateIndex
CREATE INDEX "lessonProgress_enrollmentId_idx" ON "lessonProgress"("enrollmentId");

-- CreateIndex
CREATE INDEX "lessonProgress_studentId_idx" ON "lessonProgress"("studentId");

-- CreateIndex
CREATE INDEX "lessonProgress_courseId_idx" ON "lessonProgress"("courseId");

-- CreateIndex
CREATE INDEX "lessonProgress_lessonId_idx" ON "lessonProgress"("lessonId");

-- CreateIndex
CREATE UNIQUE INDEX "lessonProgress_studentId_lessonId_key" ON "lessonProgress"("studentId", "lessonId");

-- CreateIndex
CREATE UNIQUE INDEX "quizzes_lessonId_key" ON "quizzes"("lessonId");

-- CreateIndex
CREATE INDEX "quizzes_lessonId_idx" ON "quizzes"("lessonId");

-- CreateIndex
CREATE INDEX "quizQuestions_quizId_idx" ON "quizQuestions"("quizId");

-- CreateIndex
CREATE INDEX "quizOptions_questionId_idx" ON "quizOptions"("questionId");

-- CreateIndex
CREATE INDEX "quizAttempts_quizId_idx" ON "quizAttempts"("quizId");

-- CreateIndex
CREATE INDEX "quizAttempts_studentId_idx" ON "quizAttempts"("studentId");

-- CreateIndex
CREATE INDEX "quizAttemptAnswers_attemptId_idx" ON "quizAttemptAnswers"("attemptId");

-- CreateIndex
CREATE INDEX "quizAttemptAnswers_questionId_idx" ON "quizAttemptAnswers"("questionId");

-- CreateIndex
CREATE UNIQUE INDEX "assignments_lessonId_key" ON "assignments"("lessonId");

-- CreateIndex
CREATE INDEX "assignments_lessonId_idx" ON "assignments"("lessonId");

-- CreateIndex
CREATE INDEX "assignmentSubmissions_lessonId_idx" ON "assignmentSubmissions"("lessonId");

-- CreateIndex
CREATE INDEX "assignmentSubmissions_enrollmentId_idx" ON "assignmentSubmissions"("enrollmentId");

-- CreateIndex
CREATE INDEX "assignmentSubmissions_studentId_idx" ON "assignmentSubmissions"("studentId");

-- CreateIndex
CREATE UNIQUE INDEX "assignmentSubmissions_lessonId_enrollmentId_key" ON "assignmentSubmissions"("lessonId", "enrollmentId");

-- CreateIndex
CREATE INDEX "courseRatings_courseId_idx" ON "courseRatings"("courseId");

-- CreateIndex
CREATE INDEX "courseRatings_studentId_idx" ON "courseRatings"("studentId");

-- CreateIndex
CREATE UNIQUE INDEX "courseRatings_courseId_studentId_key" ON "courseRatings"("courseId", "studentId");

-- CreateIndex
CREATE INDEX "lessonComments_lessonId_idx" ON "lessonComments"("lessonId");

-- CreateIndex
CREATE INDEX "lessonComments_studentId_idx" ON "lessonComments"("studentId");

-- CreateIndex
CREATE UNIQUE INDEX "certificates_certificateNumber_key" ON "certificates"("certificateNumber");

-- CreateIndex
CREATE UNIQUE INDEX "certificates_courseId_studentId_key" ON "certificates"("courseId", "studentId");

-- CreateIndex
CREATE UNIQUE INDEX "companies_companyEmail_key" ON "companies"("companyEmail");

-- CreateIndex
CREATE UNIQUE INDEX "payments_stripePaymentIntentId_key" ON "payments"("stripePaymentIntentId");

-- CreateIndex
CREATE INDEX "payments_studentId_idx" ON "payments"("studentId");

-- CreateIndex
CREATE INDEX "payments_courseId_idx" ON "payments"("courseId");

-- CreateIndex
CREATE INDEX "payments_status_idx" ON "payments"("status");

-- CreateIndex
CREATE UNIQUE INDEX "payments_studentId_courseId_key" ON "payments"("studentId", "courseId");

-- AddForeignKey
ALTER TABLE "courses" ADD CONSTRAINT "courses_instructorId_fkey" FOREIGN KEY ("instructorId") REFERENCES "instructors"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "courseModules" ADD CONSTRAINT "courseModules_courseId_fkey" FOREIGN KEY ("courseId") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "lessons" ADD CONSTRAINT "lessons_courseId_fkey" FOREIGN KEY ("courseId") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "lessons" ADD CONSTRAINT "lessons_moduleId_fkey" FOREIGN KEY ("moduleId") REFERENCES "courseModules"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "enrollments" ADD CONSTRAINT "enrollments_courseId_fkey" FOREIGN KEY ("courseId") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "enrollments" ADD CONSTRAINT "enrollments_studentId_fkey" FOREIGN KEY ("studentId") REFERENCES "students"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "lessonProgress" ADD CONSTRAINT "lessonProgress_courseId_fkey" FOREIGN KEY ("courseId") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "lessonProgress" ADD CONSTRAINT "lessonProgress_enrollmentId_fkey" FOREIGN KEY ("enrollmentId") REFERENCES "enrollments"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "lessonProgress" ADD CONSTRAINT "lessonProgress_lessonId_fkey" FOREIGN KEY ("lessonId") REFERENCES "lessons"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "lessonProgress" ADD CONSTRAINT "lessonProgress_studentId_fkey" FOREIGN KEY ("studentId") REFERENCES "students"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "quizzes" ADD CONSTRAINT "quizzes_lessonId_fkey" FOREIGN KEY ("lessonId") REFERENCES "lessons"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "quizQuestions" ADD CONSTRAINT "quizQuestions_quizId_fkey" FOREIGN KEY ("quizId") REFERENCES "quizzes"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "quizOptions" ADD CONSTRAINT "quizOptions_questionId_fkey" FOREIGN KEY ("questionId") REFERENCES "quizQuestions"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "quizAttempts" ADD CONSTRAINT "quizAttempts_quizId_fkey" FOREIGN KEY ("quizId") REFERENCES "quizzes"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "quizAttempts" ADD CONSTRAINT "quizAttempts_studentId_fkey" FOREIGN KEY ("studentId") REFERENCES "students"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "quizAttemptAnswers" ADD CONSTRAINT "quizAttemptAnswers_attemptId_fkey" FOREIGN KEY ("attemptId") REFERENCES "quizAttempts"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "quizAttemptAnswers" ADD CONSTRAINT "quizAttemptAnswers_questionId_fkey" FOREIGN KEY ("questionId") REFERENCES "quizQuestions"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "quizAttemptAnswers" ADD CONSTRAINT "quizAttemptAnswers_selectedOptionId_fkey" FOREIGN KEY ("selectedOptionId") REFERENCES "quizOptions"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "assignments" ADD CONSTRAINT "assignments_lessonId_fkey" FOREIGN KEY ("lessonId") REFERENCES "lessons"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "assignmentSubmissions" ADD CONSTRAINT "assignmentSubmissions_assignmentId_fkey" FOREIGN KEY ("assignmentId") REFERENCES "assignments"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "assignmentSubmissions" ADD CONSTRAINT "assignmentSubmissions_enrollmentId_fkey" FOREIGN KEY ("enrollmentId") REFERENCES "enrollments"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "assignmentSubmissions" ADD CONSTRAINT "assignmentSubmissions_gradedBy_fkey" FOREIGN KEY ("gradedBy") REFERENCES "instructors"("id") ON DELETE SET NULL ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "assignmentSubmissions" ADD CONSTRAINT "assignmentSubmissions_lessonId_fkey" FOREIGN KEY ("lessonId") REFERENCES "lessons"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "assignmentSubmissions" ADD CONSTRAINT "assignmentSubmissions_studentId_fkey" FOREIGN KEY ("studentId") REFERENCES "students"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "courseRatings" ADD CONSTRAINT "courseRatings_courseId_fkey" FOREIGN KEY ("courseId") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "courseRatings" ADD CONSTRAINT "courseRatings_studentId_fkey" FOREIGN KEY ("studentId") REFERENCES "students"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "lessonComments" ADD CONSTRAINT "lessonComments_lessonId_fkey" FOREIGN KEY ("lessonId") REFERENCES "lessons"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "lessonComments" ADD CONSTRAINT "lessonComments_studentId_fkey" FOREIGN KEY ("studentId") REFERENCES "students"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "certificates" ADD CONSTRAINT "certificates_courseId_fkey" FOREIGN KEY ("courseId") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "certificates" ADD CONSTRAINT "certificates_studentId_fkey" FOREIGN KEY ("studentId") REFERENCES "students"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "payments" ADD CONSTRAINT "payments_courseId_fkey" FOREIGN KEY ("courseId") REFERENCES "courses"("id") ON DELETE CASCADE ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "payments" ADD CONSTRAINT "payments_studentId_fkey" FOREIGN KEY ("studentId") REFERENCES "students"("id") ON DELETE CASCADE ON UPDATE CASCADE;
