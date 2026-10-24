package com.jarvis.remote

import android.content.ContentProvider
import android.content.ContentValues
import android.net.Uri
import android.os.ParcelFileDescriptor
import java.io.File

/**
 * Minimal FileProvider replacement (framework-only, no AndroidX).
 *
 * Only receives images captured via ACTION_IMAGE_CAPTURE into the app cache, so
 * `openFile` serves exactly one file name and streams it read/write for the
 * camera app that was handed the URI with a temporary grant.
 */
class PhotoProvider : ContentProvider() {

    override fun onCreate(): Boolean = true

    override fun openFile(uri: Uri, mode: String): ParcelFileDescriptor {
        val ctx = context ?: throw IllegalStateException("no context")
        val name = uri.lastPathSegment ?: "jarvis_shot.jpg"
        val file = File(ctx.cacheDir, name)
        return ParcelFileDescriptor.open(
            file,
            ParcelFileDescriptor.MODE_READ_WRITE or ParcelFileDescriptor.MODE_CREATE
        )
    }

    override fun getType(uri: Uri): String = "image/jpeg"

    override fun query(
        uri: Uri,
        projection: Array<out String>?,
        selection: String?,
        selectionArgs: Array<out String>?,
        sortOrder: String?,
    ) = null

    override fun insert(uri: Uri, values: ContentValues?): Uri? = null

    override fun delete(uri: Uri, selection: String?, selectionArgs: Array<out String>?): Int = 0

    override fun update(
        uri: Uri,
        values: ContentValues?,
        selection: String?,
        selectionArgs: Array<out String>?,
    ): Int = 0
}
