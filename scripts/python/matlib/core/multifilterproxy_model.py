"""
Provides a Model for filtering multiple Parameters at the same time
"""

from PySide6 import QtCore


class MultiFilterProxyModel(QtCore.QSortFilterProxyModel):
    """
    Provides a Model for filtering multiple Parameters at the same time
    """

    def __init__(self, parent: QtCore.QObject | None = None) -> None:
        super().__init__()
        self._filters = {}

    def setFilter(self, filter_role, filter_value):
        """
        Sets the Filter for the given role

        :param self: Description
        :param filter_role: Description
        :param filter_value: Description
        """
        self._filters[filter_role] = filter_value
        self.invalidateFilter()

    def removeFilter(self, filter_role):
        if not self._filters:
            return
        if filter_role in self._filters.keys():
            del self._filters[filter_role]
            # Refilter immediately - without this, rows stayed hidden
            # by the REMOVED filter until some other change happened to
            # invalidate the proxy (Elmar-era gap; callers papered over
            # it with their own invalidate() calls).
            self.invalidateFilter()

    def filterAcceptsRow(
        self,
        source_row: int,
        source_parent: QtCore.QModelIndex | QtCore.QPersistentModelIndex,
    ) -> bool:
        if not self._filters:
            return True

        name_filter = True
        cat_filter = True
        fav_filter = True
        tag_filter = True
        render_filter = True
        for role, curr_filter in self._filters.items():
            index = self.sourceModel().index(source_row, 0, source_parent)
            data = index.data(role)

            if role == 0:  # Check Names
                if curr_filter == "":
                    name_filter = True
                if curr_filter.lower() not in data.lower():
                    name_filter = False
            elif role == 257:  # Check Category:
                # A material matches if ANY of its categories equals the
                # filter (materials can belong to multiple categories).
                if curr_filter == "":
                    cat_filter = True
                elif len(data) < 1:
                    cat_filter = False
                else:
                    cat_filter = any(
                        curr_filter.lower() == str(elem).strip().lower()
                        for elem in data
                    )

            elif role == 258:  # Check Favorite:
                if curr_filter != data and curr_filter != "":
                    return False
            elif role == 259:  # Check Renderer:
                if curr_filter.lower() not in data.lower():
                    if "" == data:
                        render_filter = False
                    elif not "all_renderers" in curr_filter.lower():
                        render_filter = False

            elif role == 260:  # is TagRole
                # Empty filter must accept every row, including one with no
                # tags at all - checking this only inside the loop meant an
                # empty `data` list (a material with zero tags) skipped the
                # loop body entirely and fell through to the pre-loop
                # tag_filter = False, wrongly excluding untagged materials
                # even when no tag filter was active.
                if curr_filter == "":
                    tag_filter = True
                else:
                    tag_filter = any(
                        curr_filter.lower() in str(elem).lower() for elem in data
                    )

        if tag_filter and cat_filter and name_filter and fav_filter and render_filter:
            return True
        return False
