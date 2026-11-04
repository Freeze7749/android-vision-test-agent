package com.example.buggynotes

import android.app.Activity
import android.content.Context
import android.os.Bundle
import android.view.ViewGroup
import android.widget.LinearLayout
import android.widget.ScrollView

/** Notes and settings, kept in SharedPreferences so `pm clear` resets the app between test runs. */
class Store(context: Context) {
    private val prefs = context.getSharedPreferences("notes", Context.MODE_PRIVATE)

    /** The single seeded bug that is switched on, or "" for the correct app. */
    var bug: String
        get() = prefs.getString("bug", "") ?: ""
        set(value) = prefs.edit().putString("bug", value).apply()

    var showCount: Boolean
        get() = prefs.getBoolean("showCount", false)
        set(value) = prefs.edit().putBoolean("showCount", value).apply()

    /** Note titles, one per line. Keeping only titles is enough for these tests. */
    var titles: List<String>
        get() = (prefs.getString("titles", SEED) ?: SEED).split("\n").filter { it.isNotEmpty() }
        set(value) = prefs.edit().putString("titles", value.joinToString("\n")).apply()

    private companion object {
        const val SEED = "Welcome\nMeeting notes\nShopping list"
    }
}

/** A scrolling vertical page, with room for the status bar, shared by all screens. */
fun Activity.page(): LinearLayout {
    val column = LinearLayout(this).apply {
        orientation = LinearLayout.VERTICAL
        setPadding(48, 160, 48, 48)
    }
    setContentView(ScrollView(this).apply {
        addView(column, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT))
    })
    return column
}

abstract class StoreActivity : Activity() {
    protected lateinit var store: Store

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        store = Store(this)
    }
}
